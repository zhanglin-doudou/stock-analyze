"""指标执行引擎（Period2-05，§6.2）：拓扑序 + 门禁 + read-through 缓存。

职责边界：
- 引擎只做「框架代算」（状态机、sample_status、CI、assumptions、指纹），不做任何指标口径；
- `compute` 是同步纯函数；持久化由 `persist.py`（编排层）负责，便于单测无 DB 运行；
- blocked/stale 直接短路（不 compute、不读缓存、不落库）；unreliable 仍 compute 但不落库。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any, cast

from stock_contracts.constants import A_SHUFFLE_DAYS, ENGINE_VERSION, MIN_CONCLUSION_SAMPLE
from stock_contracts.errors import ApiError, ErrorCode

from stock_review.metrics.base import (
    MetricContext,
    MetricDef,
    MetricValue,
    assert_no_status_leak,
)
from stock_review.metrics.context import UpstreamInfo
from stock_review.metrics.estimators import compute_ci
from stock_review.metrics.gate import (
    REASON_PENDING_WINDOW,
    STATUS_BLOCKED,
    STATUS_OK,
    STATUS_STALE,
    GateDecision,
    dataset_dependencies,
    decide,
    metric_dependencies,
)
from stock_review.metrics.hash import inputs_hash, params_hash

PENDING_SAMPLE_STATUS = "pending_window"
INSUFFICIENT_SAMPLE_STATUS = "insufficient"
OK_SAMPLE_STATUS = "ok"
BLOCKED_SAMPLE_STATUS = "blocked"

CacheKey = tuple[str, str, str]


@dataclass(frozen=True, slots=True)
class MetricOutcome:
    """单指标执行结果（含持久化所需的全部字段，但不含 ORM）。"""

    metric_id: str
    definition_version: str
    domain: str
    scope: Mapping[str, str]
    period: str
    params: Mapping[str, Any]
    params_hash: str
    inputs_hash: str
    status: str
    sample_status: str
    reason: str | None
    value_units: int | None
    unit: str
    precision: int
    n: int
    n_pending: int
    ci_low_units: int | None
    ci_high_units: int | None
    decomposition: Mapping[str, Any]
    assumptions: Mapping[str, Any]
    data_as_of: date
    computed_at: datetime
    cached: bool = False
    lineage: tuple[Mapping[str, Any], ...] = field(default_factory=tuple)

    @property
    def persistable(self) -> bool:
        return self.status == STATUS_OK

    @property
    def conclusion_available(self) -> bool:
        return (
            self.status == STATUS_OK
            and self.sample_status == OK_SAMPLE_STATUS
            and self.n >= MIN_CONCLUSION_SAMPLE
        )


def _as_of_candidates(dataset: str, ctx: MetricContext) -> date | None:
    if dataset == "rounds":
        values = [row.close_date or row.open_date for row in ctx.rounds]
        return max(values) if values else None
    if dataset == "fills":
        return max((row.close_date for row in ctx.fills), default=None)
    if dataset == "trades":
        return max((row.trade_date for row in ctx.trades), default=None)
    if dataset == "asset_daily":
        return max((row.trade_date for row in ctx.asset_daily), default=None)
    if dataset == "asset_build":
        value = ctx.asset_build.data_as_of
        return value if isinstance(value, date) else None
    if dataset == "bars_daily":
        return max((row.trade_date for row in ctx.bars), default=None)
    if dataset == "benchmark_series":
        return max((row.trade_date for row in ctx.benchmark_series), default=None)
    if dataset == "calendar":
        return min(ctx.as_of, ctx.period_end)
    return None


def _data_as_of(definition: MetricDef, ctx: MetricContext) -> date:
    candidates = [
        value
        for dataset in sorted(dataset_dependencies(definition))
        if (value := _as_of_candidates(dataset, ctx)) is not None
    ]
    if candidates:
        return min(candidates)
    return min(ctx.as_of, ctx.period_end)


def _base_assumptions(ctx: MetricContext) -> dict[str, Any]:
    assumptions: dict[str, Any] = {
        "rf_rate": format(ctx.rf_annual, "f")
        if isinstance(ctx.rf_annual, Decimal)
        else str(ctx.rf_annual),
        "A": A_SHUFFLE_DAYS,
        "engine_version": ENGINE_VERSION,
        "calendar": getattr(ctx.calendar, "assumption", "weekday_proxy"),
        "period_basis": "natural_month",
        "adjust": "raw",
    }
    if ctx.benchmark is not None:
        assumptions["benchmark_id"] = ctx.benchmark
    return assumptions


def _build_params(
    definition: MetricDef, ctx: MetricContext, overrides: Mapping[str, Any]
) -> dict[str, Any]:
    try:
        validated = definition.params_schema.model_validate(overrides)
    except Exception as exc:  # pydantic ValidationError 不直接透出字段细节
        raise ApiError(
            ErrorCode.VALIDATION_ERROR,
            detail=f"{definition.id} 参数非法",
        ) from exc
    return {
        "benchmark": ctx.benchmark,
        "rf_annual": format(ctx.rf_annual, "f"),
        "params": validated.model_dump(mode="json"),
    }


def _inputs_payload(
    definition: MetricDef,
    ctx: MetricContext,
    *,
    params_fingerprint: str,
    upstream_hashes: Mapping[str, str],
) -> dict[str, Any]:
    datasets = {
        name: ctx.fingerprints.get(name) for name in sorted(dataset_dependencies(definition))
    }
    return {
        "metric_id": definition.id,
        "definition_version": definition.definition_version,
        "engine_version": ENGINE_VERSION,
        "params_hash": params_fingerprint,
        "period": ctx.period,
        "period_start": ctx.period_start.isoformat(),
        "period_end": ctx.period_end.isoformat(),
        "as_of": ctx.as_of.isoformat(),
        "benchmark": ctx.benchmark,
        "rf_annual": format(ctx.rf_annual, "f"),
        "datasets": datasets,
        "upstream": {key: upstream_hashes[key] for key in sorted(upstream_hashes)},
    }


def _sample_status(definition: MetricDef, value: MetricValue, ctx: MetricContext) -> str:
    policy = definition.sample_policy
    if value.n < policy.min_n:
        return INSUFFICIENT_SAMPLE_STATUS
    if policy.min_trading_days > 0 and ctx.observed_trading_days < policy.min_trading_days:
        return PENDING_SAMPLE_STATUS
    return OK_SAMPLE_STATUS


def _cached_outcome(
    definition: MetricDef,
    row: Any,
    *,
    ctx: MetricContext,
    params: Mapping[str, Any],
    params_fingerprint: str,
    inputs_fingerprint: str,
) -> MetricOutcome:
    return MetricOutcome(
        metric_id=definition.id,
        definition_version=definition.definition_version,
        domain=definition.domain,
        scope=definition.scope.to_dict(),
        period=ctx.period,
        params=params,
        params_hash=params_fingerprint,
        inputs_hash=inputs_fingerprint,
        status=STATUS_OK,
        sample_status=row.sample_status,
        reason=row.reason,
        value_units=row.value_units,
        unit=definition.unit,
        precision=definition.precision,
        n=row.n,
        n_pending=row.n_pending,
        ci_low_units=row.ci_low_units,
        ci_high_units=row.ci_high_units,
        decomposition=dict(row.decomposition or {}),
        assumptions=dict(row.assumptions or {}),
        data_as_of=row.data_as_of,
        computed_at=row.computed_at,
        cached=True,
    )


def _blocked_outcome(
    definition: MetricDef,
    ctx: MetricContext,
    *,
    decision: GateDecision,
    params: Mapping[str, Any],
    params_fingerprint: str,
    inputs_fingerprint: str,
    computed_at: datetime,
) -> MetricOutcome:
    return MetricOutcome(
        metric_id=definition.id,
        definition_version=definition.definition_version,
        domain=definition.domain,
        scope=definition.scope.to_dict(),
        period=ctx.period,
        params=params,
        params_hash=params_fingerprint,
        inputs_hash=inputs_fingerprint,
        status=decision.status,
        sample_status=BLOCKED_SAMPLE_STATUS,
        reason=decision.reason,
        value_units=None,
        unit=definition.unit,
        precision=definition.precision,
        n=0,
        n_pending=0,
        ci_low_units=None,
        ci_high_units=None,
        decomposition={},
        assumptions=_base_assumptions(ctx),
        data_as_of=_data_as_of(definition, ctx),
        computed_at=computed_at,
    )


class _ParamsContext:
    """把当前指标校验后的 params 绑定到 ctx 的只读代理（compute 读 `ctx.params`）。"""

    __slots__ = ("_base", "params")

    def __init__(self, base: MetricContext, params: Mapping[str, Any]) -> None:
        self._base = base
        self.params = dict(params)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._base, name)


def _lineage(
    definition: MetricDef,
    ctx: MetricContext,
    upstream_hashes: Mapping[str, str],
) -> tuple[Mapping[str, Any], ...]:
    rows: list[Mapping[str, Any]] = []
    for name in sorted(dataset_dependencies(definition)):
        rows.append(
            {
                "dep_kind": "dataset",
                "dep_id": name,
                "dep_hash": ctx.fingerprints.get(name),
            }
        )
    for metric_id in metric_dependencies(definition):
        rows.append(
            {
                "dep_kind": "metric",
                "dep_id": metric_id,
                "dep_hash": upstream_hashes.get(metric_id),
            }
        )
    return tuple(rows)


def evaluate_metrics(
    definitions: Sequence[MetricDef],
    ctx: MetricContext,
    *,
    params_by_metric: Mapping[str, Mapping[str, Any]],
    cached: Mapping[CacheKey, Any],
    computed_at: datetime,
) -> list[MetricOutcome]:
    """按传入顺序（已拓扑排序）执行；`ctx.upstream` 会随执行补齐供下游使用。"""
    outcomes: list[MetricOutcome] = []
    upstream_hashes: dict[str, str] = {}

    for definition in definitions:
        params = _build_params(definition, ctx, params_by_metric.get(definition.id, {}))
        params_fingerprint = params_hash(params)
        inputs_fingerprint = inputs_hash(
            _inputs_payload(
                definition,
                ctx,
                params_fingerprint=params_fingerprint,
                upstream_hashes=upstream_hashes,
            )
        )

        decision = decide(definition, ctx)
        if decision.status in (STATUS_BLOCKED, STATUS_STALE):
            outcome = _blocked_outcome(
                definition,
                ctx,
                decision=decision,
                params=params,
                params_fingerprint=params_fingerprint,
                inputs_fingerprint=inputs_fingerprint,
                computed_at=computed_at,
            )
            outcomes.append(outcome)
            ctx.upstream[definition.id] = UpstreamInfo(outcome.status, outcome.inputs_hash)
            continue

        key = (definition.id, definition.definition_version, params_fingerprint)
        row = cached.get(key)
        if (
            row is not None
            and row.inputs_hash == inputs_fingerprint
            and row.status == STATUS_OK
            and decision.status == STATUS_OK
        ):
            outcome = _cached_outcome(
                definition,
                row,
                ctx=ctx,
                params=params,
                params_fingerprint=params_fingerprint,
                inputs_fingerprint=inputs_fingerprint,
            )
            outcomes.append(outcome)
            upstream_hashes[definition.id] = outcome.inputs_hash
            ctx.upstream[definition.id] = UpstreamInfo(outcome.status, outcome.inputs_hash)
            continue

        bound = cast(MetricContext, _ParamsContext(ctx, params["params"]))
        value = definition.compute(bound)
        assert_no_status_leak(value)
        sample_status = _sample_status(definition, value, ctx)
        status = decision.status
        ci_low: int | None = None
        ci_high: int | None = None
        if status == STATUS_OK and sample_status == OK_SAMPLE_STATUS:
            ci_low, ci_high = compute_ci(definition, value, inputs_hash=inputs_fingerprint)

        value_units = value.value_units
        if status != STATUS_OK or sample_status == PENDING_SAMPLE_STATUS:
            value_units = None

        reason = decision.reason
        if status == STATUS_OK and sample_status == PENDING_SAMPLE_STATUS:
            reason = REASON_PENDING_WINDOW

        outcome = MetricOutcome(
            metric_id=definition.id,
            definition_version=definition.definition_version,
            domain=definition.domain,
            scope=definition.scope.to_dict(),
            period=ctx.period,
            params=params,
            params_hash=params_fingerprint,
            inputs_hash=inputs_fingerprint,
            status=status,
            sample_status=sample_status,
            reason=reason,
            value_units=value_units,
            unit=definition.unit,
            precision=definition.precision,
            n=value.n,
            n_pending=value.n_pending,
            ci_low_units=ci_low,
            ci_high_units=ci_high,
            decomposition=dict(value.decomposition) if status == STATUS_OK else {},
            assumptions=_base_assumptions(ctx),
            data_as_of=_data_as_of(definition, ctx),
            computed_at=computed_at,
            lineage=_lineage(definition, ctx, upstream_hashes) if status == STATUS_OK else (),
        )
        outcomes.append(outcome)
        upstream_hashes[definition.id] = outcome.inputs_hash
        ctx.upstream[definition.id] = UpstreamInfo(outcome.status, outcome.inputs_hash)

    return outcomes


__all__ = [
    "BLOCKED_SAMPLE_STATUS",
    "INSUFFICIENT_SAMPLE_STATUS",
    "OK_SAMPLE_STATUS",
    "PENDING_SAMPLE_STATUS",
    "MetricOutcome",
    "evaluate_metrics",
]
