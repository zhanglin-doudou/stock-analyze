"""L1 golden：生产引擎输出 == golden.expected（§8.1/§8.2）。

- 每个 ``golden/<metric_id>/golden*.json`` 一条参数化用例；
- value 走 ``support/tolerance.py`` 容差；n/CI/decomposition 精确比对（CI 确定性，D-07）；
- 期望值来源 = 手算表/独立 oracle，**禁止**用生产代码生成后回填。
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable
from datetime import UTC, datetime

import pytest
from stock_review.metrics.context import MetricContextData
from stock_review.metrics.engine import evaluate_metrics
from stock_review.metrics.registry import build_registry

from golden.loader import GoldenCase, load_all
from support.fx_metrics_core import (
    make_data_error_context,
    make_fx_context,
    make_hhi_small_context,
    make_n5_context,
    make_n10_context,
)
from support.tolerance import assert_within_tolerance, parse_tolerance

pytestmark = pytest.mark.golden

FIXTURE_BUILDERS: dict[str, Callable[[], MetricContextData]] = {
    "fx_metrics_core": make_fx_context,
    "fx_hhi_small": make_hhi_small_context,
    "fx_n5": make_n5_context,
    "fx_n10": make_n10_context,
    "fx_data_error": make_data_error_context,
}

_COMPUTED_AT = datetime(2026, 9, 30, 15, 0, tzinfo=UTC)

_CASES = load_all()
_REGISTRY = build_registry()


def _case_id(case: GoldenCase) -> str:
    return f"{case.metric_id}@{case.path.parent.name}/{case.path.stem}"


@pytest.mark.parametrize("case", _CASES, ids=[_case_id(case) for case in _CASES])
def test_golden(case: GoldenCase) -> None:
    definition = _REGISTRY.require(case.metric_id)
    assert definition.definition_version == case.definition_version, (
        f"{case.metric_id}: golden version={case.definition_version} "
        f"≠ def version={definition.definition_version}（口径升级需升 definition_version）"
    )
    builder = FIXTURE_BUILDERS.get(case.fixture_id)
    assert builder is not None, f"未知 fixture: {case.fixture_id}"
    ctx = builder()
    if case.fixture_flags:
        ctx = dataclasses.replace(ctx, flags={**ctx.flags, **case.fixture_flags})
    assert ctx.period == case.period

    outcomes = evaluate_metrics(
        [definition],
        ctx,
        params_by_metric={},
        cached={},
        computed_at=_COMPUTED_AT,
    )
    assert len(outcomes) == 1
    outcome = outcomes[0]

    label = f"{case.metric_id}@{case.definition_version}"
    assert outcome.status == case.status, f"{label}: status {outcome.status!r} ≠ {case.status!r}"
    assert outcome.sample_status == case.sample_status, (
        f"{label}: sample_status {outcome.sample_status!r} ≠ {case.sample_status!r}"
    )
    assert outcome.n == case.n, f"{label}: n {outcome.n} ≠ {case.n}"
    assert outcome.unit == case.unit, f"{label}: unit {outcome.unit!r} ≠ {case.unit!r}"

    tolerance = parse_tolerance(case.tolerance, precision=definition.precision)
    assert_within_tolerance(
        outcome.value_units,
        case.value_units(definition.precision),
        tolerance=tolerance,
        path=f"{label}.value",
    )

    # CI 确定性：逐位相等（容差不适用于 CI 出口）
    assert outcome.ci_low_units == case.ci_low_units(definition.precision), (
        f"{label}.ci_low: {outcome.ci_low_units} ≠ {case.ci_low_units(definition.precision)}"
    )
    assert outcome.ci_high_units == case.ci_high_units(definition.precision), (
        f"{label}.ci_high: {outcome.ci_high_units} ≠ {case.ci_high_units(definition.precision)}"
    )

    # decomposition：键集合 + 逐键精确（防键漂移）
    actual_decomp = dict(outcome.decomposition)
    assert set(actual_decomp) == set(case.decomposition), (
        f"{label}.decomposition 键漂移: "
        f"多 {sorted(set(actual_decomp) - set(case.decomposition))} / "
        f"少 {sorted(set(case.decomposition) - set(actual_decomp))}"
    )
    for key, expected in case.decomposition.items():
        actual = actual_decomp[key]
        assert isinstance(actual, int) or actual is None, (
            f"{label}.decomposition.{key}: 非整数 {actual!r}"
        )
        assert actual == expected, (
            f"{label}.decomposition.{key}: {actual} ≠ {expected}"
        )
