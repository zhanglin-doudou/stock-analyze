"""门禁状态机与物化写回单测（Period2-05）：优先级、负缓存、确定性主键。"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel, ConfigDict
from stock_review.metrics.base import (
    DatasetDep,
    DrilldownSpec,
    ExplainSpec,
    MetricContext,
    MetricValue,
    SamplePolicy,
    ScopeSpec,
)
from stock_review.metrics.context import UpstreamInfo
from stock_review.metrics.engine import evaluate_metrics
from stock_review.metrics.gate import decide
from stock_review.metrics.persist import (
    dependency_id,
    dependency_rows,
    is_stale,
    metric_result_id,
    persist_outcome,
    result_values,
)
from stock_review.metrics.registry import build_registry

from support.engine_kit import assert_no_float
from support.metrics_factories import USER_ID, make_context, make_rounds

SUPPORT_DEFS = Path(__file__).resolve().parents[2] / "support" / "metric_defs"
COMPUTED_AT = datetime(2026, 6, 30, 16, 0, tzinfo=UTC)


class NoParams(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _def(datasets: tuple[str, ...] = (), *, min_trading_days: int = 0) -> Any:
    class _Def:
        id = "perf.gate_probe"
        definition_version = "1.0"
        domain = "perf"
        scope = ScopeSpec(period="month")
        params_schema = NoParams
        dependencies = tuple(DatasetDep(name) for name in datasets)
        sample_policy = SamplePolicy(min_trading_days=min_trading_days)
        status_policy = ()
        unit = "count"
        precision = 0
        core_conclusion = False
        frequency = "sync"
        benchmark_required = False
        mvp = "M1"
        explain = ExplainSpec(conclusion_zh="c", conclusion_en="c", method_zh="m", method_en="m")
        drilldown = DrilldownSpec(
            endpoint="/api/v1/rounds",
            params={"period": "{period}"},
            description_zh="d",
            description_en="d",
            build_items=lambda ctx, filters: (),
        )

        def compute(self, ctx: MetricContext) -> MetricValue:
            return MetricValue(value_units=1, n=1)

        def validate_config(self) -> None:
            return None

    definition = _Def()
    if not datasets:
        definition.dependencies = ()
    return definition


def test_ok_when_no_flags() -> None:
    decision = decide(_def(("rounds",)), make_context())
    assert decision.status == "ok"
    assert decision.reason is None
    assert decision.compute is True
    assert decision.ok is True


@pytest.mark.parametrize(
    ("datasets", "flags", "reason"),
    [
        (("bars_daily",), {"dataset_missing:bars_daily": True}, "DATASET_UNAVAILABLE:bars_daily"),
        (("benchmark_series",), {"dataset_missing:benchmark_series": True}, "BENCHMARK_MISSING"),
        (("asset_build",), {"asset_build_missing": True}, "ASSET_BUILD_BLOCKED"),
        (("asset_build",), {"asset_build_blocked": True}, "ASSET_BUILD_BLOCKED"),
        (("asset_daily",), {"recon_blocked": True}, "RECON_BLOCKED"),
    ],
)
def test_blocked_branches(datasets: tuple[str, ...], flags: dict[str, bool], reason: str) -> None:
    decision = decide(_def(datasets), make_context(flags=flags))
    assert decision.status == "blocked"
    assert decision.reason == reason
    assert decision.compute is False


def test_upstream_blocked_takes_priority() -> None:
    definition = _def(("asset_build",))
    ctx = make_context(flags={"asset_build_missing": True, "asset_build_stale": True})
    ctx.upstream["perf.upstream"] = UpstreamInfo(status="blocked", inputs_hash="sha256:" + "x" * 64)
    definition.dependencies = (DatasetDep("asset_build"),)
    from stock_review.metrics.base import MetricDep

    definition.dependencies = (*definition.dependencies, MetricDep("perf.upstream"))
    decision = decide(definition, ctx)
    assert decision.status == "blocked"
    assert decision.reason == "UPSTREAM_BLOCKED:perf.upstream"


@pytest.mark.parametrize(
    ("datasets", "flags", "reason"),
    [
        (("asset_build",), {"asset_build_stale": True}, "ASSET_BUILD_STALE"),
        (("bars_daily",), {"market_asof_lag": True}, "MARKET_ASOF_LAG"),
        (("benchmark_series",), {"market_asof_lag": True}, "MARKET_ASOF_LAG"),
    ],
)
def test_stale_branches(datasets: tuple[str, ...], flags: dict[str, bool], reason: str) -> None:
    decision = decide(_def(datasets), make_context(flags=flags))
    assert decision.status == "stale"
    assert decision.reason == reason
    assert decision.compute is False


def test_stale_beats_unreliable_and_does_not_block_rounds_only() -> None:
    ctx = make_context(flags={"asset_build_stale": True, "data_out_of_range": True})
    decision = decide(_def(("asset_build",)), ctx)
    assert decision.status == "stale"

    # rounds-only 指标不受 asset_build 状态影响（data_out_of_range 仍是全局样本问题）
    rounds_ctx = make_context(flags={"asset_build_stale": True})
    assert decide(_def(("rounds",)), rounds_ctx).status == "ok"


def test_unreliable_branches() -> None:
    out_of_range = decide(_def(("rounds",)), make_context(flags={"data_out_of_range": True}))
    assert out_of_range.status == "unreliable"
    assert out_of_range.reason == "DATA_OUT_OF_RANGE"
    assert out_of_range.compute is True

    low_density = decide(
        _def(("bars_daily",), min_trading_days=60),
        make_context(flags={"bar_density_low": True}),
    )
    assert low_density.status == "unreliable"
    assert low_density.reason == "BAR_DENSITY_LOW"

    # min_trading_days=0 时不看 bar_density_low
    assert (
        decide(_def(("bars_daily",)), make_context(flags={"bar_density_low": True})).status == "ok"
    )

    degraded = decide(_def(("asset_daily",)), make_context(flags={"degraded_rows": True}))
    assert degraded.status == "unreliable"
    assert degraded.reason == "DATA_DEGRADED"


def test_persist_ids_are_deterministic() -> None:
    first = metric_result_id(
        USER_ID,
        metric_id="perf.x",
        period="2026-06",
        definition_version="1.0",
        params_hash_value="a" * 64,
    )
    second = metric_result_id(
        USER_ID,
        metric_id="perf.x",
        period="2026-06",
        definition_version="1.0",
        params_hash_value="a" * 64,
    )
    assert first == second
    assert first.startswith("mtr_")
    assert len(first) == 4 + 32

    dep = dependency_id(
        USER_ID,
        metric_id="perf.x",
        period="2026-06",
        definition_version="1.0",
        dep_kind="dataset",
        dep_id="rounds",
    )
    assert dep.startswith("mtd_")


def test_is_stale_matrix() -> None:
    class Row:
        inputs_hash = "sha256:" + "a" * 64
        status = "ok"

    assert is_stale(Row(), inputs_hash="sha256:" + "a" * 64) is False
    assert is_stale(Row(), inputs_hash="sha256:" + "b" * 64) is True
    Row.status = "unreliable"
    assert is_stale(Row(), inputs_hash=Row.inputs_hash) is True
    assert is_stale(object(), inputs_hash="x") is True


class _FakeRepository:
    def __init__(self) -> None:
        self.upserts: list[dict[str, Any]] = []
        self.replacements: list[dict[str, Any]] = []
        self.user_ids: list[uuid.UUID] = []

    async def upsert(self, user_id: uuid.UUID, values: dict[str, Any]) -> None:
        self.user_ids.append(user_id)
        self.upserts.append(values)

    async def replace_dependencies(self, user_id: uuid.UUID, **kwargs: Any) -> None:
        self.user_ids.append(user_id)
        self.replacements.append(kwargs)


async def test_persist_ok_and_skip_non_ok() -> None:
    registry = build_registry(extra_paths=[SUPPORT_DEFS])
    definition = registry.require("perf.win_rate_stub")
    outcome = evaluate_metrics(
        [definition],
        make_context(rounds=make_rounds()),
        params_by_metric={},
        cached={},
        computed_at=COMPUTED_AT,
    )[0]

    repository = _FakeRepository()
    assert await persist_outcome(repository, USER_ID, outcome) is True  # type: ignore[arg-type]
    assert len(repository.upserts) == 1
    values = repository.upserts[0]
    assert values["metric_id"] == definition.id
    assert values["value_units"] == outcome.value_units
    assert_no_float(values)
    assert repository.replacements[0]["definition_version"] == "1.0"

    blocked = _blocked_outcome(outcome)
    assert await persist_outcome(repository, USER_ID, blocked) is False  # type: ignore[arg-type]
    assert len(repository.upserts) == 1


def _blocked_outcome(outcome: Any) -> Any:
    from dataclasses import replace

    return replace(outcome, status="blocked", value_units=None, lineage=())


def test_result_values_and_dependency_rows() -> None:
    registry = build_registry(extra_paths=[SUPPORT_DEFS])
    definition = registry.require("perf.win_rate_stub")
    outcome = evaluate_metrics(
        [definition],
        make_context(rounds=make_rounds()),
        params_by_metric={},
        cached={},
        computed_at=COMPUTED_AT,
    )[0]

    values = result_values(USER_ID, outcome)
    assert values["period"] == "2026-06"
    assert values["inputs_hash"] == outcome.inputs_hash

    rows = dependency_rows(USER_ID, outcome)
    assert [row["dep_id"] for row in rows] == ["rounds"]
    assert rows[0]["dep_kind"] == "dataset"
    assert rows[0]["dep_hash"] == outcome.lineage[0]["dep_hash"]
