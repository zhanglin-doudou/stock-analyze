"""指标端点（API-033/034/035/079，Period2-06）。

统一约定：
- 所有端点走 read-through 引擎（不直接读 `metric_result`），保证 blocked/unreliable
  也能解释/下钻；
- ETag 为弱校验 `W/"<sha16>"`，`If-None-Match` 命中 → 304 空 body，且 **304 判定在
  UPSERT 之前**（无写副作用）；
- admin 不可读业务指标（403 `ADMIN_CANNOT_READ_TRADES`）；
- 路径参数 `metric_id` 先做 dotted 校验，非法一律 404 `METRIC_NOT_FOUND`。
"""

from __future__ import annotations

from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Query, Request, Response
from stock_contracts.errors import ApiError, ErrorCode
from stock_platform.tenant import TenantContext
from stock_review.application.metrics import (
    batch_etag,
    batch_metrics,
    catalog_metrics,
    detail_metric,
    drilldown_metric,
    explain_metric,
    get_registry,
    persist_safely,
)
from stock_review.metrics.base import METRIC_ID_PATTERN, format_units
from stock_review.metrics.context import period_is_cacheable
from stock_review.metrics.engine import MetricOutcome
from stock_review.metrics.presentation import build_presentation

from stock_api.deps import get_state, get_tenant
from stock_api.routing import StrictQueryRoute
from stock_api.schemas import (
    MetricBatchOut,
    MetricCatalogOut,
    MetricDrilldownItemOut,
    MetricDrilldownOut,
    MetricExplainDrilldown,
    MetricExplainOut,
    MetricExplainSample,
    MetricItemOut,
    MetricPresentationOut,
    MetricScopeOut,
)

MAX_IDS = 50
_ETAG_304: dict[int | str, dict[str, Any]] = {
    304: {"description": "Not Modified（If-None-Match 命中）"}
}

router = APIRouter(tags=["metrics"], route_class=StrictQueryRoute)


def _if_none_match_hit(header_value: str | None, etag: str) -> bool:
    """条件 GET：忽略 `W/` 弱校验前缀与引号后逐字比较。"""
    if not header_value:
        return False
    candidate = header_value.strip()
    if candidate.startswith("W/"):
        candidate = candidate[2:]
    return candidate.strip('"') == etag


def _parse_ids(raw: str | None) -> list[str] | None:
    """`ids` 逗号分隔 → 去重保序；空/全空白 → 422；超过 50 → 422。"""
    if raw is None:
        return None
    parts: list[str] = []
    seen: set[str] = set()
    for chunk in raw.split(","):
        metric_id = chunk.strip()
        if not metric_id or metric_id in seen:
            continue
        seen.add(metric_id)
        parts.append(metric_id)
    if not parts:
        raise ApiError(ErrorCode.VALIDATION_ERROR, detail="ids 不能为空")
    if len(parts) > MAX_IDS:
        raise ApiError(
            ErrorCode.VALIDATION_ERROR,
            detail=f"ids 最多 {MAX_IDS} 个，实际 {len(parts)} 个",
        )
    return parts


def _require_metric_id(metric_id: str) -> str:
    if not METRIC_ID_PATTERN.fullmatch(metric_id):
        raise ApiError(ErrorCode.METRIC_NOT_FOUND, detail=f"未知指标: {metric_id}")
    return metric_id


def _scope_out(outcome: MetricOutcome) -> MetricScopeOut:
    return MetricScopeOut(
        unit=str(outcome.scope.get("unit", "month")),
        cohort=str(outcome.scope.get("cohort", "all")),
        period=str(outcome.scope.get("period", "month")),
    )


def _item_out(outcome: MetricOutcome) -> MetricItemOut:
    """引擎 outcome（标度整数）→ API 边界模型（定点字符串）。"""
    try:
        definition = get_registry().require(outcome.metric_id)
        presentation = MetricPresentationOut(**build_presentation(outcome, definition))
    except KeyError:  # pragma: no cover - registry 与 outcome 同源，仅防御
        presentation = None
    return MetricItemOut(
        metric_id=outcome.metric_id,
        definition_version=outcome.definition_version,
        domain=outcome.domain,
        scope=_scope_out(outcome),
        period=outcome.period,
        status=outcome.status,
        sample_status=outcome.sample_status,
        reason=outcome.reason,
        value=format_units(outcome.value_units, outcome.unit, outcome.precision),
        unit=outcome.unit,
        precision=outcome.precision,
        n=outcome.n,
        n_pending=outcome.n_pending,
        ci_low=format_units(outcome.ci_low_units, outcome.unit, outcome.precision),
        ci_high=format_units(outcome.ci_high_units, outcome.unit, outcome.precision),
        decomposition=dict(outcome.decomposition) if outcome.decomposition else None,
        assumptions=dict(outcome.assumptions),
        data_as_of=outcome.data_as_of,
        inputs_hash=outcome.inputs_hash,
        computed_at=outcome.computed_at,
        presentation=presentation,
    )


@router.get(
    "/metrics",
    response_model=MetricBatchOut,
    operation_id="metrics_batch",
    responses=_ETAG_304,
)
async def metrics_batch_endpoint(
    request: Request,
    response: Response,
    ctx: Annotated[TenantContext, Depends(get_tenant)],
    ids: Annotated[str | None, Query()] = None,
    domain: Annotated[str | None, Query()] = None,
    period: Annotated[str | None, Query()] = None,
    date_from: Annotated[date | None, Query(alias="from")] = None,
    date_to: Annotated[date | None, Query(alias="to")] = None,
    benchmark: Annotated[str | None, Query()] = None,
    include_exploratory: Annotated[bool, Query()] = False,
) -> MetricBatchOut | Response:
    """API-033：指标批量查询（只物化 ok；304 无副作用）。"""
    ctx.forbid_admin_trades()
    user_id = ctx.require_user()
    state = get_state(request)
    parsed_ids = _parse_ids(ids)

    async with state.db.session(user_id=user_id, begin=True) as session:
        # 廉价 ETag 只读本地库（不含行情拉取/指标计算）：命中即 304，避免完整 batch。
        etag_value = await batch_etag(
            session,
            user_id=user_id,
            clock=state.clock,
            market_data=state.market_data,
            rf_annual=state.settings.rf_annual,
            period=period,
            date_from=date_from,
            date_to=date_to,
            benchmark=benchmark,
            ids=parsed_ids,
            domain=domain,
            include_exploratory=include_exploratory,
        )
        etag_header = f'W/"{etag_value}"'
        if _if_none_match_hit(request.headers.get("if-none-match"), etag_value):
            return Response(status_code=304, headers={"ETag": etag_header})
        result = await batch_metrics(
            session,
            user_id=user_id,
            clock=state.clock,
            market_data=state.market_data,
            rf_annual=state.settings.rf_annual,
            period=period,
            date_from=date_from,
            date_to=date_to,
            benchmark=benchmark,
            ids=parsed_ids,
            domain=domain,
            include_exploratory=include_exploratory,
            etag=etag_value,
        )
        await persist_safely(session, user_id, result.all_outcomes, cacheable=result.cacheable)

    response.headers["ETag"] = etag_header
    return MetricBatchOut(
        period=result.period,
        data_as_of=result.data_as_of,
        definition_version=result.definition_version,
        assumptions=result.assumptions,
        items=[_item_out(outcome) for outcome in result.items],
    )


@router.get(
    "/metrics/catalog",
    response_model=MetricCatalogOut,
    operation_id="metrics_catalog",
)
async def metrics_catalog_endpoint(
    ctx: Annotated[TenantContext, Depends(get_tenant)],
) -> MetricCatalogOut:
    """API-086：18 个指标的口径说明书（静态元数据；无用户数据/无副作用）。"""
    ctx.require_user()
    ctx.forbid_admin_trades()
    registry = get_registry()
    return MetricCatalogOut(
        registry_version=registry.version,
        items=list(catalog_metrics(registry)),
    )


@router.get(
    "/metrics/{metric_id}/explain",
    response_model=MetricExplainOut,
    operation_id="metrics_explain",
)
async def metrics_explain_endpoint(
    metric_id: str,
    request: Request,
    ctx: Annotated[TenantContext, Depends(get_tenant)],
    period: Annotated[str, Query()],
    benchmark: Annotated[str | None, Query()] = None,
    accept_language: Annotated[str | None, Header(alias="Accept-Language")] = None,
) -> MetricExplainOut:
    """API-034：三段式解释（结论/口径/下钻；无裸术语）。"""
    ctx.forbid_admin_trades()
    user_id = ctx.require_user()
    state = get_state(request)
    _require_metric_id(metric_id)

    async with state.db.session(user_id=user_id, begin=True) as session:
        result = await explain_metric(
            session,
            user_id=user_id,
            clock=state.clock,
            market_data=state.market_data,
            rf_annual=state.settings.rf_annual,
            metric_id=metric_id,
            period=period,
            date_from=None,
            date_to=None,
            benchmark=benchmark,
            accept_language=accept_language,
        )
    return MetricExplainOut(
        metric_id=result.metric_id,
        definition_version=result.definition_version,
        period=result.period,
        meaning=result.meaning,
        conclusion=result.conclusion,
        method=result.method,
        domain=result.domain,
        unit=result.unit,
        precision=result.precision,
        sample=MetricExplainSample(**result.sample),
        drilldown=MetricExplainDrilldown(
            endpoint=str(result.drilldown["endpoint"]),
            params=dict(result.drilldown.get("params") or {}),
            description=str(result.drilldown["description"]),
        ),
        status=result.status,
        reason=result.reason,
        what_to_do=result.what_to_do,
        assumptions=result.assumptions,
        data_as_of=result.data_as_of,
        presentation=MetricPresentationOut(**result.presentation),
    )


@router.get(
    "/metrics/{metric_id}/drilldown",
    response_model=MetricDrilldownOut,
    operation_id="metrics_drilldown",
)
async def metrics_drilldown_endpoint(
    metric_id: str,
    request: Request,
    ctx: Annotated[TenantContext, Depends(get_tenant)],
    period: Annotated[str, Query()],
    benchmark: Annotated[str | None, Query()] = None,
    round_id: Annotated[str | None, Query()] = None,
    security_code: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    cursor: Annotated[str | None, Query()] = None,
) -> MetricDrilldownOut:
    """API-035：指标下钻明细（def 提供 build_items；引擎只做排序/分页）。"""
    ctx.forbid_admin_trades()
    user_id = ctx.require_user()
    state = get_state(request)
    _require_metric_id(metric_id)

    async with state.db.session(user_id=user_id, begin=True) as session:
        result = await drilldown_metric(
            session,
            user_id=user_id,
            clock=state.clock,
            market_data=state.market_data,
            rf_annual=state.settings.rf_annual,
            metric_id=metric_id,
            period=period,
            date_from=None,
            date_to=None,
            benchmark=benchmark,
            round_id=round_id,
            security_code=security_code,
            limit=limit,
            cursor=cursor,
        )
    return MetricDrilldownOut(
        metric_id=result.metric_id,
        definition_version=result.definition_version,
        period=result.period,
        status=result.status,
        reason=result.reason,
        items=[MetricDrilldownItemOut(**item) for item in result.items],
        total=result.total,
        source_endpoints=list(result.source_endpoints),
        params=dict(result.params),
        next_cursor=result.next_cursor,
        truncated=result.truncated,
        assumptions=result.assumptions,
        data_as_of=result.data_as_of,
    )


@router.get(
    "/metrics/{metric_id}",
    response_model=MetricItemOut,
    operation_id="metrics_detail",
    responses=_ETAG_304,
)
async def metrics_detail_endpoint(
    metric_id: str,
    request: Request,
    response: Response,
    ctx: Annotated[TenantContext, Depends(get_tenant)],
    period: Annotated[str, Query()],
    benchmark: Annotated[str | None, Query()] = None,
) -> MetricItemOut | Response:
    """API-079：单指标完整结果（与 API-033 同 period/params 的 item 逐字段相等）。"""
    ctx.forbid_admin_trades()
    user_id = ctx.require_user()
    state = get_state(request)
    _require_metric_id(metric_id)

    async with state.db.session(user_id=user_id, begin=True) as session:
        outcome, etag, _ = await detail_metric(
            session,
            user_id=user_id,
            clock=state.clock,
            market_data=state.market_data,
            rf_annual=state.settings.rf_annual,
            metric_id=metric_id,
            period=period,
            date_from=None,
            date_to=None,
            benchmark=benchmark,
        )
        etag_header = f'W/"{etag}"'
        if _if_none_match_hit(request.headers.get("if-none-match"), etag):
            return Response(status_code=304, headers={"ETag": etag_header})
        await persist_safely(session, user_id, [outcome], cacheable=period_is_cacheable(period))

    response.headers["ETag"] = etag_header
    return _item_out(outcome)
