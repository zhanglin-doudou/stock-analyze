"""05 引擎契约测试：样本门禁、非 ok 无 value、无 float、纯度 AST。"""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel, ConfigDict
from stock_review.metrics.base import (
    DatasetDep,
    DrilldownSpec,
    ExplainSpec,
    MetricContext,
    MetricDef,
    MetricDep,
    MetricValue,
    SamplePolicy,
    ScopeSpec,
)
from stock_review.metrics.engine import evaluate_metrics
from stock_review.metrics.registry import build_registry

from support.engine_kit import (
    assert_blocked_has_no_value,
    assert_compute_is_pure,
    assert_no_float,
    assert_sample_gate,
)
from support.metrics_factories import make_context, make_rounds

SUPPORT_DEFS = Path(__file__).resolve().parents[2] / "support" / "metric_defs"
COMPUTED_AT = datetime(2026, 6, 30, 16, 0, tzinfo=UTC)


class NoParams(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _explain() -> ExplainSpec:
    return ExplainSpec(
        conclusion_zh="结论",
        conclusion_en="Conclusion",
        method_zh="口径",
        method_en="Method",
    )


def _drilldown() -> DrilldownSpec:
    return DrilldownSpec(
        endpoint="/api/v1/rounds",
        params={"period": "{period}"},
        description_zh="明细",
        description_en="Details",
        build_items=lambda ctx, filters: (),
    )


class _BarsDef:
    id = "perf.bars_only"
    definition_version = "1.0"
    domain = "perf"
    scope = ScopeSpec(period="month")
    params_schema = NoParams
    dependencies = (DatasetDep("bars_daily"),)
    sample_policy = SamplePolicy()
    status_policy = ()
    unit = "count"
    precision = 0
    core_conclusion = False
    frequency = "sync"
    benchmark_required = False
    mvp = "M1"
    explain = _explain()
    drilldown = _drilldown()

    def compute(self, ctx: MetricContext) -> MetricValue:
        return MetricValue(value_units=1, n=1)

    def validate_config(self) -> None:
        return None


class _DependentDef(_BarsDef):
    id = "perf.depends_on_bars"
    dependencies = (MetricDep("perf.bars_only"),)


class _PendingDef(_BarsDef):
    id = "perf.pending_window"
    dependencies = (DatasetDep("rounds"),)
    sample_policy = SamplePolicy(min_n=0, ci_min_n=0, ci="none", min_trading_days=60)

    def compute(self, ctx: MetricContext) -> MetricValue:
        return MetricValue(value_units=1_0000_0000, n=12)


def test_no_float_guard_is_recursive() -> None:
    assert_no_float({"a": 1, "b": ["x", {"c": None}], "d": {"e": 100}})
    with pytest.raises(AssertionError, match="float"):
        assert_no_float({"a": [{"b": 1.5}]})
    with pytest.raises(AssertionError, match="float"):
        assert_no_float(0.1)


def test_win_rate_happy_path_with_wilson_ci() -> None:
    registry = build_registry(extra_paths=[SUPPORT_DEFS])
    definition = registry.require("perf.win_rate_stub")
    ctx = make_context(rounds=make_rounds(wins=7, losses=5))

    outcomes = evaluate_metrics(
        [definition], ctx, params_by_metric={}, cached={}, computed_at=COMPUTED_AT
    )

    outcome = outcomes[0]
    assert outcome.status == "ok"
    assert outcome.sample_status == "ok"
    assert outcome.n == 12
    assert outcome.value_units == 58_333_333  # 7/12
    assert outcome.ci_low_units is not None and outcome.ci_high_units is not None
    assert outcome.ci_low_units < outcome.value_units < outcome.ci_high_units
    assert outcome.inputs_hash.startswith("sha256:")
    assert len(outcome.inputs_hash) == 71
    assert outcome.data_as_of == date(2026, 6, 10)
    assert_no_float(dict(outcome.assumptions))
    assert_sample_gate(outcome, definition)


def test_insufficient_sample_keeps_value_for_context() -> None:
    registry = build_registry(extra_paths=[SUPPORT_DEFS])
    definition = registry.require("perf.win_rate_stub")
    ctx = make_context(rounds=make_rounds(wins=2, losses=1))

    outcome = evaluate_metrics(
        [definition], ctx, params_by_metric={}, cached={}, computed_at=COMPUTED_AT
    )[0]

    assert outcome.sample_status == "insufficient"
    assert outcome.status == "ok"
    assert outcome.value_units is not None
    assert outcome.ci_low_units is None  # CI 仅在 sample_status=ok 时计算


def test_pending_window_has_no_value() -> None:
    ctx = make_context(rounds=make_rounds(), observed_trading_days=20)
    outcome = evaluate_metrics(
        [_PendingDef()], ctx, params_by_metric={}, cached={}, computed_at=COMPUTED_AT
    )[0]

    assert outcome.status == "ok"
    assert outcome.sample_status == "pending_window"
    assert outcome.value_units is None
    assert outcome.reason == "PENDING_WINDOW"
    assert outcome.persistable is True  # 只物化 ok（含 pending_window），供下次直接命中


def test_blocked_propagates_to_dependent_metric() -> None:
    ctx = make_context(flags={"dataset_missing:bars_daily": True})
    definitions: list[MetricDef] = [_BarsDef(), _DependentDef()]

    outcomes = evaluate_metrics(
        definitions, ctx, params_by_metric={}, cached={}, computed_at=COMPUTED_AT
    )

    upstream, dependent = outcomes
    assert upstream.status == "blocked"
    assert upstream.reason == "DATASET_UNAVAILABLE:bars_daily"
    assert_blocked_has_no_value(upstream)

    assert dependent.status == "blocked"
    assert dependent.reason == "UPSTREAM_BLOCKED:perf.bars_only"
    assert_blocked_has_no_value(dependent)


def test_bootstrap_ci_uses_registered_estimator() -> None:
    registry = build_registry(extra_paths=[SUPPORT_DEFS])
    definition = registry.require("perf.expectancy_stub")
    ctx = make_context(rounds=make_rounds(wins=7, losses=5))

    outcome = evaluate_metrics(
        [definition], ctx, params_by_metric={}, cached={}, computed_at=COMPUTED_AT
    )[0]

    assert outcome.status == "ok"
    assert outcome.n == 12
    assert outcome.ci_low_units is not None
    assert outcome.ci_high_units is not None
    assert outcome.ci_low_units <= outcome.value_units <= outcome.ci_high_units


def test_cache_hit_requires_matching_inputs_hash() -> None:
    registry = build_registry(extra_paths=[SUPPORT_DEFS])
    definition = registry.require("perf.win_rate_stub")
    ctx = make_context(rounds=make_rounds())

    first = evaluate_metrics(
        [definition], ctx, params_by_metric={}, cached={}, computed_at=COMPUTED_AT
    )[0]
    key = (first.metric_id, first.definition_version, first.params_hash)

    hit = evaluate_metrics(
        [definition], ctx, params_by_metric={}, cached={key: first}, computed_at=COMPUTED_AT
    )[0]
    assert hit.cached is True
    assert hit.value_units == first.value_units

    ctx.fingerprints["rounds"] = "sha256:" + "b" * 64
    miss = evaluate_metrics(
        [definition], ctx, params_by_metric={}, cached={key: first}, computed_at=COMPUTED_AT
    )[0]
    assert miss.cached is False
    assert miss.inputs_hash != first.inputs_hash


def test_params_are_validated_and_bound_to_compute() -> None:
    registry = build_registry(extra_paths=[SUPPORT_DEFS])
    definition = registry.require("perf.win_rate_stub")
    ctx = make_context(rounds=make_rounds(wins=3, losses=2))

    outcome = evaluate_metrics(
        [definition],
        ctx,
        params_by_metric={definition.id: {"min_holding_days": 6}},
        cached={},
        computed_at=COMPUTED_AT,
    )[0]

    assert outcome.n == 0  # 5 天持仓全部被过滤
    assert outcome.sample_status == "insufficient"

    with pytest.raises(Exception, match="参数非法"):
        evaluate_metrics(
            [definition],
            ctx,
            params_by_metric={definition.id: {"bogus": 1}},
            cached={},
            computed_at=COMPUTED_AT,
        )


def test_test_defs_are_pure_and_production_registry_is_empty() -> None:
    for path in sorted(SUPPORT_DEFS.glob("*.py")):
        if not path.name.startswith("_"):
            assert_compute_is_pure(path)

    production = build_registry()
    assert "perf.win_rate_stub" not in production
    assert len(production) == 0


def test_outcome_dict_contains_no_float() -> None:
    registry = build_registry(extra_paths=[SUPPORT_DEFS])
    definition = registry.require("perf.win_rate_stub")
    outcome = evaluate_metrics(
        [definition],
        make_context(rounds=make_rounds()),
        params_by_metric={},
        cached={},
        computed_at=COMPUTED_AT,
    )[0]

    payload: dict[str, Any] = {
        "value_units": outcome.value_units,
        "ci_low_units": outcome.ci_low_units,
        "ci_high_units": outcome.ci_high_units,
        "decomposition": dict(outcome.decomposition),
        "assumptions": dict(outcome.assumptions),
    }
    assert_no_float(payload)
