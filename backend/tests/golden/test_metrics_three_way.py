"""L3 三方对拍（§8.5）：生产 == golden、oracle == golden、生产 == oracle。

- ① 生产引擎输出 vs golden.expected（与 ``test_metrics_golden.py`` 同口径，走容差入口）；
- ② 独立 oracle（``tests/oracle/naive_*.py``，只用标准库）vs golden；
- ③ 生产 vs oracle（同一夹具、同一定义版本）；
- ④ oracle 确定性自检：同输入两次调用结果必须逐位一致。

非 ``ok`` 状态（blocked/stale/unreliable）生产不进入计算（decomposition={}），
oracle 不参与对拍；``pending_window`` 生产强制 value=None，oracle 侧同样抑制后比较。
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import pytest
from stock_review.metrics.context import MetricContextData
from stock_review.metrics.engine import evaluate_metrics
from stock_review.metrics.registry import build_registry

from golden.loader import GoldenCase, load_all
from golden.test_metrics_golden import FIXTURE_BUILDERS
from oracle import naive_bhar, naive_metrics, naive_timing
from oracle.naive_metrics import OracleResult
from support.tolerance import assert_within_tolerance, parse_tolerance

pytestmark = pytest.mark.golden

_COMPUTED_AT = datetime(2026, 9, 30, 15, 0, tzinfo=UTC)
_CASES = load_all()
_REGISTRY = build_registry()

ORACLE: dict[str, Callable[[MetricContextData], OracleResult]] = {
    "perf.win_rate": naive_metrics.win_rate,
    "perf.pnl_ratio": naive_metrics.pnl_ratio,
    "perf.expectancy": naive_metrics.expectancy,
    "perf.concentration": naive_metrics.concentration,
    "perf.holding_days": naive_metrics.holding_days,
    "perf.fee": naive_metrics.fee,
    "perf.fee_breakeven": naive_metrics.fee_breakeven,
    "behavior.turnover": naive_metrics.turnover,
    "behavior.averaging_down": naive_metrics.averaging_down,
    "behavior.de": naive_metrics.behavior_de,
    "behavior.hhi": naive_metrics.hhi,
    "timing.chase": naive_timing.chase,
    "timing.fly": naive_timing.fly,
    "timing.beta": naive_timing.beta,
    "timing.drawdown": naive_timing.drawdown,
    "selection.bhar_20": naive_bhar.bhar_20,
    "selection.bhar_60": naive_bhar.bhar_60,
    "selection.bhar_hit": naive_bhar.bhar_hit,
}


def _case_id(case: GoldenCase) -> str:
    return f"{case.metric_id}@{case.path.parent.name}/{case.path.stem}"


def _context(case: GoldenCase) -> MetricContextData:
    builder = FIXTURE_BUILDERS.get(case.fixture_id)
    assert builder is not None, f"未知 fixture: {case.fixture_id}"
    ctx = builder()
    if case.fixture_flags:
        ctx = dataclasses.replace(ctx, flags={**ctx.flags, **case.fixture_flags})
    return ctx


def _assert_decomposition(actual: dict[str, Any], expected: dict[str, Any], *, path: str) -> None:
    assert set(actual) == set(expected), (
        f"{path}: 键漂移 多={sorted(set(actual) - set(expected))} "
        f"少={sorted(set(expected) - set(actual))}"
    )
    for key, want in expected.items():
        assert actual[key] == want, f"{path}.{key}: {actual[key]!r} ≠ {want!r}"


@pytest.mark.parametrize("case", _CASES, ids=[_case_id(case) for case in _CASES])
def test_three_way(case: GoldenCase) -> None:
    definition = _REGISTRY.require(case.metric_id)
    oracle_fn = ORACLE.get(case.metric_id)
    assert oracle_fn is not None, f"缺少 oracle 实现: {case.metric_id}"

    expected_value = case.value_units(definition.precision)
    tolerance = parse_tolerance(case.tolerance, precision=definition.precision)
    label = f"{case.metric_id}@{definition.definition_version}"

    # ① 生产 == golden
    prod_ctx = _context(case)
    outcomes = evaluate_metrics(
        [definition], prod_ctx, params_by_metric={}, cached={}, computed_at=_COMPUTED_AT
    )
    assert len(outcomes) == 1
    production = outcomes[0]
    assert_within_tolerance(
        production.value_units, expected_value, tolerance=tolerance, path=f"{label}.value"
    )
    _assert_decomposition(
        dict(production.decomposition), case.decomposition, path=f"{label}.decomposition"
    )

    if case.status != "ok":
        # 非 ok 状态生产短路（不计算），oracle 不参与对拍（golden 已锁状态口径）。
        assert production.decomposition == {}
        return

    # ② oracle == golden（先做确定性自检）
    first = oracle_fn(_context(case))
    second = oracle_fn(_context(case))
    assert first == second, f"{label}: oracle 结果不确定"

    oracle_units = naive_metrics.units_of(first.value, definition.precision)
    if case.sample_status == "pending_window":
        # 生产引擎在窗口未满时强制不出口 value（value=None），oracle 侧同口径抑制。
        oracle_units = None
    assert_within_tolerance(
        oracle_units, expected_value, tolerance=tolerance, path=f"{label}.oracle.value"
    )
    assert first.n == case.n, f"{label}: oracle n={first.n} ≠ golden n={case.n}"
    _assert_decomposition(
        first.decomposition, case.decomposition, path=f"{label}.oracle.decomposition"
    )

    # ③ 生产 == oracle
    assert_within_tolerance(
        production.value_units,
        oracle_units,
        tolerance=tolerance,
        path=f"{label}.production_vs_oracle.value",
    )
    assert production.n == first.n, f"{label}: production n={production.n} ≠ oracle n={first.n}"
    _assert_decomposition(
        dict(production.decomposition), first.decomposition, path=f"{label}.production_vs_oracle"
    )
