"""指标应用层（Period2-06）：读通缓存 + 引擎编排 + 四个端点的 DTO 组装。

GET 的 read-through 写副作用（C-36）：
- 本层只负责「算」与「读」；UPSERT 由路由在 **ETag 304 判定之后** 调 `persist_safely`，
  保证 304 请求无副作用；
- 写失败不阻断读（日志 + savepoint 回滚），本次仍返回计算结果。
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession
from stock_contracts.clock import Clock
from stock_contracts.constants import A_SHUFFLE_DAYS, ENGINE_VERSION
from stock_contracts.errors import ApiError, ErrorCode
from stock_platform.cursor import decode_cursor, encode_cursor
from stock_platform.ports import MarketDataSource

from stock_review.metrics.base import (
    UNIT_PRECISION,
    DrilldownFilters,
    DrilldownItem,
    MetricDef,
    MetricDep,
    format_units,
)
from stock_review.metrics.context import MetricContextData, Period, resolve_period
from stock_review.metrics.datasets import DEFAULT_BENCHMARK, load_viewset
from stock_review.metrics.engine import (
    MetricOutcome,
    evaluate_metrics,
)
from stock_review.metrics.gate import decide
from stock_review.metrics.hash import canonical_json, sha256_hex
from stock_review.metrics.persist import persist_outcome
from stock_review.metrics.registry import MetricRegistry, build_registry
from stock_review.metrics.views import ViewSet
from stock_review.repositories.metrics import MetricResultRepository

logger = logging.getLogger(__name__)

KNOWN_BENCHMARKS: frozenset[str] = frozenset({"hs300_tr", "zz500_tr", "csi1000_tr"})
DOMAIN_FILTERS: frozenset[str] = frozenset(
    {"all", "perf", "timing", "behavior", "selection", "attribution"}
)
EXPLORATORY_MVP = "M2"
DRILLDOWN_MAX_ITEMS = 5000

_REGISTRY: MetricRegistry | None = None


def get_registry() -> MetricRegistry:
    """进程级注册表（代码静态；测试可 `set_registry_for_tests` 注入 stub）。"""
    global _REGISTRY
    if _REGISTRY is None:
        _REGISTRY = build_registry()
    return _REGISTRY


def set_registry_for_tests(registry: MetricRegistry | None) -> None:
    global _REGISTRY
    _REGISTRY = registry


@dataclass(slots=True)
class BatchResult:
    period: str
    data_as_of: date
    definition_version: str
    assumptions: dict[str, Any]
    items: list[MetricOutcome]
    all_outcomes: list[MetricOutcome]
    etag: str


@dataclass(slots=True)
class ExplainResult:
    metric_id: str
    definition_version: str
    period: str
    conclusion: str | None
    method: str
    drilldown: dict[str, Any]
    status: str
    reason: str | None
    what_to_do: str | None
    assumptions: dict[str, Any]
    data_as_of: date


@dataclass(slots=True)
class DrilldownResult:
    metric_id: str
    definition_version: str
    period: str
    status: str
    reason: str | None
    items: list[dict[str, Any]] = field(default_factory=list)
    total: int = 0
    source_endpoints: list[str] = field(default_factory=list)
    params: dict[str, Any] = field(default_factory=dict)
    next_cursor: str | None = None
    truncated: bool = False
    assumptions: dict[str, Any] = field(default_factory=dict)
    data_as_of: date | None = None


def _resolve_benchmark(requested: str | None, definitions: Sequence[MetricDef]) -> str | None:
    if requested is not None:
        if requested not in KNOWN_BENCHMARKS:
            raise ApiError(
                ErrorCode.UNKNOWN_BENCHMARK,
                detail=f"未知 benchmark: {requested}",
            )
        return requested
    if any(definition.benchmark_required for definition in definitions):
        return DEFAULT_BENCHMARK
    return None


def _select_definitions(
    registry: MetricRegistry,
    *,
    ids: Sequence[str] | None,
    domain: str | None,
    include_exploratory: bool,
) -> tuple[list[MetricDef], list[str]]:
    """返回（执行用 defs 顺序, 选中 ids 顺序）。执行顺序 = 选中集合的依赖闭包拓扑序。"""
    if ids:
        if domain is not None:
            raise ApiError(ErrorCode.VALIDATION_ERROR, detail="ids 与 domain 只能二选一")
        ordered: list[str] = []
        seen: set[str] = set()
        for metric_id in ids:
            if metric_id in seen:
                continue
            if registry.get(metric_id) is None:
                raise ApiError(ErrorCode.METRIC_NOT_FOUND, detail=f"未知指标: {metric_id}")
            seen.add(metric_id)
            ordered.append(metric_id)
        selected_ids = ordered
    else:
        domain_value = domain or "all"
        if domain_value not in DOMAIN_FILTERS:
            raise ApiError(ErrorCode.VALIDATION_ERROR, detail=f"非法 domain: {domain_value}")
        selected_ids = [
            definition.id
            for definition in registry.by_domain(domain_value)
            if include_exploratory or definition.mvp != EXPLORATORY_MVP
        ]

    closure: set[str] = set()

    def add(metric_id: str) -> None:
        if metric_id in closure:
            return
        closure.add(metric_id)
        definition = registry.get(metric_id)
        if definition is None:
            return
        for dependency in definition.dependencies:
            if isinstance(dependency, MetricDep):
                add(dependency.metric_id)

    for metric_id in selected_ids:
        add(metric_id)
    ordered_defs = [definition for definition in registry.topological() if definition.id in closure]
    return ordered_defs, selected_ids


async def _build_context(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    period: Period,
    benchmark: str | None,
    clock: Clock,
    market_data: MarketDataSource,
    rf_annual: Decimal,
) -> tuple[ViewSet, MetricContextData]:
    views = await load_viewset(
        session,
        user_id=user_id,
        period_start=period.start,
        period_end=period.end,
        benchmark=benchmark,
        market_data=market_data,
        clock=clock,
    )
    as_of = clock.today()
    if views.market_as_of is not None:
        as_of = min(as_of, views.market_as_of)
    if views.asset_build.data_as_of is not None:
        as_of = min(as_of, views.asset_build.data_as_of)
    ctx = MetricContextData(
        user_id=user_id,
        period=period.label,
        period_start=period.start,
        period_end=period.end,
        as_of=as_of,
        params={},
        benchmark=benchmark,
        rf_annual=rf_annual,
        rounds=views.rounds,
        fills=views.fills,
        trades=views.trades,
        asset_daily=views.asset_daily,
        asset_build=views.asset_build,
        bars=views.bars,
        benchmark_series=views.benchmark,
        calendar=views.calendar,
        flags=dict(views.flags),
        upstream={},
        fingerprints=dict(views.fingerprints),
        market_as_of=views.market_as_of,
        engine_version=ENGINE_VERSION,
        observed_trading_days=views.observed_trading_days,
    )
    return views, ctx


def _item_etag_payload(route: str, items: Sequence[MetricOutcome], extra: Mapping[str, Any]) -> str:
    payload = {
        "route": route,
        **dict(extra),
        "items": [
            {
                "metric_id": item.metric_id,
                "definition_version": item.definition_version,
                "params_hash": item.params_hash,
                "inputs_hash": item.inputs_hash,
                "status": item.status,
            }
            for item in sorted(items, key=lambda outcome: outcome.metric_id)
        ],
    }
    return sha256_hex(canonical_json(payload))[:16]


def _top_level(
    items: Sequence[MetricOutcome],
    *,
    benchmark: str | None,
    registry: MetricRegistry,
    fallback_as_of: date,
) -> tuple[str, date, dict[str, Any]]:
    versions = {item.definition_version for item in items}
    definition_version = versions.pop() if len(versions) == 1 else "mixed"
    data_as_of = min(item.data_as_of for item in items) if items else fallback_as_of
    assumptions: dict[str, Any] = {
        "rf_rate": items[0].assumptions.get("rf_rate", "0.015") if items else "0.015",
        "A": A_SHUFFLE_DAYS,
        "engine_version": ENGINE_VERSION,
        "calendar": "weekday_proxy",
        "registry_version": registry.version,
    }
    if benchmark is not None:
        assumptions["benchmark_id"] = benchmark
    return definition_version, data_as_of, assumptions


async def batch_metrics(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    clock: Clock,
    market_data: MarketDataSource,
    rf_annual: Decimal,
    period: str | None,
    date_from: date | None,
    date_to: date | None,
    benchmark: str | None,
    ids: Sequence[str] | None,
    domain: str | None,
    include_exploratory: bool,
    registry: MetricRegistry | None = None,
) -> BatchResult:
    resolved = resolve_period(
        period=period, date_from=date_from, date_to=date_to, today=clock.today()
    )
    active_registry = registry or get_registry()
    executing, selected_ids = _select_definitions(
        active_registry, ids=ids, domain=domain, include_exploratory=include_exploratory
    )
    selected_benchmark = _resolve_benchmark(benchmark, executing)

    _, ctx = await _build_context(
        session,
        user_id=user_id,
        period=resolved,
        benchmark=selected_benchmark,
        clock=clock,
        market_data=market_data,
        rf_annual=rf_annual,
    )

    repository = MetricResultRepository(session)
    cached = await repository.rows_for_period(
        user_id,
        period=resolved.label,
        metric_ids=[definition.id for definition in executing],
    )
    outcomes = evaluate_metrics(
        executing,
        ctx,
        params_by_metric={},
        cached=cached,
        computed_at=clock.now(),
    )
    by_id = {outcome.metric_id: outcome for outcome in outcomes}
    items = [by_id[metric_id] for metric_id in selected_ids if metric_id in by_id]
    definition_version, data_as_of, assumptions = _top_level(
        items,
        benchmark=selected_benchmark,
        registry=active_registry,
        fallback_as_of=resolved.end,
    )
    etag = _item_etag_payload(
        "metrics",
        items,
        {
            "ids": list(selected_ids),
            "domain": domain,
            "period": resolved.label,
            "benchmark": selected_benchmark,
            "include_exploratory": include_exploratory,
            "registry_version": active_registry.version,
        },
    )
    return BatchResult(
        period=resolved.label,
        data_as_of=data_as_of,
        definition_version=definition_version,
        assumptions=assumptions,
        items=list(items),
        all_outcomes=list(outcomes),
        etag=etag,
    )


async def detail_metric(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    clock: Clock,
    market_data: MarketDataSource,
    rf_annual: Decimal,
    metric_id: str,
    period: str | None,
    date_from: date | None,
    date_to: date | None,
    benchmark: str | None,
    registry: MetricRegistry | None = None,
) -> tuple[MetricOutcome, str, dict[str, Any]]:
    result = await batch_metrics(
        session,
        user_id=user_id,
        clock=clock,
        market_data=market_data,
        rf_annual=rf_annual,
        period=period,
        date_from=date_from,
        date_to=date_to,
        benchmark=benchmark,
        ids=[metric_id],
        domain=None,
        include_exploratory=True,
        registry=registry,
    )
    outcome = result.items[0]
    return outcome, result.etag, result.assumptions


async def persist_safely(
    session: AsyncSession, user_id: uuid.UUID, outcomes: Sequence[MetricOutcome]
) -> int:
    """逐指标 savepoint 写回；失败只记日志，不影响响应（E-01）。"""
    repository = MetricResultRepository(session)
    written = 0
    for outcome in outcomes:
        if not outcome.persistable:
            continue
        try:
            async with session.begin_nested():
                await persist_outcome(repository, user_id, outcome)
            written += 1
        except Exception:
            logger.warning("metric persist failed: %s", outcome.metric_id, exc_info=True)
    return written


def _language(value: str | None) -> str:
    if value and value.lower().startswith("en"):
        return "en"
    return "zh"


async def explain_metric(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    clock: Clock,
    market_data: MarketDataSource,
    rf_annual: Decimal,
    metric_id: str,
    period: str | None,
    date_from: date | None,
    date_to: date | None,
    benchmark: str | None,
    accept_language: str | None,
    registry: MetricRegistry | None = None,
) -> ExplainResult:
    active_registry = registry or get_registry()
    definition = active_registry.get(metric_id)
    if definition is None:
        raise ApiError(ErrorCode.METRIC_NOT_FOUND, detail=f"未知指标: {metric_id}")
    outcome, _, assumptions = await detail_metric(
        session,
        user_id=user_id,
        clock=clock,
        market_data=market_data,
        rf_annual=rf_annual,
        metric_id=metric_id,
        period=period,
        date_from=date_from,
        date_to=date_to,
        benchmark=benchmark,
        registry=active_registry,
    )
    lang = _language(accept_language)
    explain = definition.explain
    if outcome.conclusion_available:
        conclusion = explain.conclusion_en if lang == "en" else explain.conclusion_zh
        what_to_do = None
        reason = None
    else:
        conclusion = None
        reason = outcome.reason or "INSUFFICIENT_SAMPLE"
        what_to_do = (explain.what_to_do_en if lang == "en" else explain.what_to_do_zh) or (
            "Increase the sample size or wait for the window to complete."
            if lang == "en"
            else "增加样本或等待窗口走完后再看结论。"
        )
    return ExplainResult(
        metric_id=metric_id,
        definition_version=definition.definition_version,
        period=outcome.period,
        conclusion=conclusion,
        method=explain.method_en if lang == "en" else explain.method_zh,
        drilldown={
            "endpoint": definition.drilldown.endpoint,
            "params": dict(definition.drilldown.params),
            "description": (
                definition.drilldown.description_en
                if lang == "en"
                else definition.drilldown.description_zh
            ),
        },
        status=outcome.status,
        reason=reason,
        what_to_do=what_to_do,
        assumptions=assumptions,
        data_as_of=outcome.data_as_of,
    )


def _sort_key(item: DrilldownItem) -> tuple[str, str]:
    return (item.event_date.isoformat() if item.event_date else "", item.key)


def _drilldown_fingerprint(
    *, metric_id: str, period: str, benchmark: str | None, filters: Mapping[str, Any]
) -> str:
    return sha256_hex(
        canonical_json(
            {
                "metric_id": metric_id,
                "period": period,
                "benchmark": benchmark,
                "filters": dict(filters),
            }
        )
    )[:16]


def _format_item(item: DrilldownItem) -> dict[str, Any]:
    precision = UNIT_PRECISION[item.unit]
    payload: dict[str, Any] = {
        "key": item.key,
        "value": format_units(item.value_units, item.unit, precision),
        "unit": item.unit,
        "label": item.label,
    }
    if item.round_id is not None:
        payload["round_id"] = item.round_id
    if item.trade_id is not None:
        payload["trade_id"] = item.trade_id
    if item.event_date is not None:
        payload["event_date"] = item.event_date.isoformat()
    if item.security_code is not None:
        payload["security_code"] = item.security_code
    return payload


async def drilldown_metric(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    clock: Clock,
    market_data: MarketDataSource,
    rf_annual: Decimal,
    metric_id: str,
    period: str | None,
    date_from: date | None,
    date_to: date | None,
    benchmark: str | None,
    round_id: str | None,
    security_code: str | None,
    limit: int,
    cursor: str | None,
    registry: MetricRegistry | None = None,
) -> DrilldownResult:
    active_registry = registry or get_registry()
    definition = active_registry.get(metric_id)
    if definition is None:
        raise ApiError(ErrorCode.METRIC_NOT_FOUND, detail=f"未知指标: {metric_id}")
    resolved = resolve_period(
        period=period, date_from=date_from, date_to=date_to, today=clock.today()
    )
    selected_benchmark = _resolve_benchmark(benchmark, (definition,))
    _, ctx = await _build_context(
        session,
        user_id=user_id,
        period=resolved,
        benchmark=selected_benchmark,
        clock=clock,
        market_data=market_data,
        rf_annual=rf_annual,
    )

    decision = decide(definition, ctx)

    def _make_result(
        *,
        status: str,
        reason: str | None,
        items: list[dict[str, Any]] | None = None,
        total: int = 0,
        next_cursor: str | None = None,
        truncated: bool = False,
    ) -> DrilldownResult:
        params: dict[str, Any] = {"metric_id": metric_id, "period": resolved.label}
        if selected_benchmark is not None:
            params["benchmark"] = selected_benchmark
        return DrilldownResult(
            metric_id=metric_id,
            definition_version=definition.definition_version,
            period=resolved.label,
            status=status,
            reason=reason,
            items=items or [],
            total=total,
            source_endpoints=[definition.drilldown.endpoint],
            params=params,
            next_cursor=next_cursor,
            truncated=truncated,
            assumptions={
                "rf_rate": format(rf_annual, "f"),
                "A": A_SHUFFLE_DAYS,
                "engine_version": ENGINE_VERSION,
            },
            data_as_of=min(ctx.as_of, resolved.end),
        )

    if decision.status != "ok":
        return _make_result(status=decision.status, reason=decision.reason)

    filters = DrilldownFilters(
        period=resolved.label,
        benchmark=selected_benchmark,
        round_id=round_id,
        security_code=security_code,
    )
    raw_items = list(definition.drilldown.build_items(ctx, filters))
    raw_items.sort(key=_sort_key)
    truncated = len(raw_items) > DRILLDOWN_MAX_ITEMS
    if truncated:
        raw_items = raw_items[:DRILLDOWN_MAX_ITEMS]

    fingerprint = _drilldown_fingerprint(
        metric_id=metric_id,
        period=resolved.label,
        benchmark=selected_benchmark,
        filters={"round_id": round_id, "security_code": security_code},
    )
    start = 0
    if cursor is not None:
        payload = decode_cursor(cursor)
        try:
            if payload.get("f") != fingerprint:
                raise ApiError(ErrorCode.INVALID_CURSOR, detail="游标与本次查询不匹配")
            cursor_key = (str(payload["d"]), str(payload["k"]))
            for index, item in enumerate(raw_items):
                if _sort_key(item) == cursor_key:
                    start = index + 1
                    break
            else:
                raise ApiError(ErrorCode.INVALID_CURSOR, detail="游标位置不存在")
        except (KeyError, ValueError) as exc:
            raise ApiError(ErrorCode.INVALID_CURSOR, detail="游标字段缺失") from exc

    capped = max(1, min(limit, 200))
    page = raw_items[start : start + capped]
    next_cursor = None
    if start + capped < len(raw_items) and page:
        last = page[-1]
        next_cursor = encode_cursor(
            {
                "d": last.event_date.isoformat() if last.event_date else "",
                "k": last.key,
                "f": fingerprint,
            }
        )
    return _make_result(
        status="ok",
        reason=None,
        items=[_format_item(item) for item in page],
        total=len(raw_items),
        next_cursor=next_cursor,
        truncated=truncated,
    )


__all__ = [
    "DRILLDOWN_MAX_ITEMS",
    "KNOWN_BENCHMARKS",
    "BatchResult",
    "DrilldownResult",
    "ExplainResult",
    "batch_metrics",
    "detail_metric",
    "drilldown_metric",
    "explain_metric",
    "get_registry",
    "persist_safely",
    "set_registry_for_tests",
]
