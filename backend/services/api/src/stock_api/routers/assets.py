"""资产表端点（API-081/082，Period2-16）：每日资产快照列表 + 构建/增量更新任务。

* `GET /assets`（`listAssets`）：纯读，**不落库、不触发构建**；支持游标分页、日期闭区间
  过滤、状态过滤与白名单排序；金额/比率在本层转**定点字符串**，blocked 行数值显式 `null`；
  响应带 `build` 摘要块（state/last_* /假设/是否新行情），前端零口径。
* `POST /assets/build`（`buildAssets`）：长任务 → `build_assets` job → 202 + Location/
  Retry-After；`Idempotency-Key` 必填（request_hash 基于**解析默认值后**的规范 body）；
  同参单飞复用同一 job（`X-Job-Reused`），异参 → 409；幂等重放带 `Idempotency-Replayed`。

写作用域：本文件只依赖 `stock_review.application.assets` 的冻结签名（impl-contract §2.4），
**延迟 import** 以保证 review 包尚未落盘时 app 仍可装配（endpoint 调用时才需要）。
跨账号/不存在一律 404，admin 不可读写业务数据（身份越权 → 403）。
"""

from __future__ import annotations

import hashlib
import json
from datetime import date
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Body, Depends, Header, Query, Request, Response
from fastapi.responses import JSONResponse
from stock_contracts.errors import ApiError, ErrorCode, FieldError
from stock_core.values import Money, Ratio
from stock_platform.idempotency import IdempotencyStore
from stock_platform.tenant import TenantContext

from stock_api.deps import get_market_data, get_state, get_tenant
from stock_api.routing import StrictQueryRoute
from stock_api.schemas import (
    AssetBuildAccepted,
    AssetBuildIn,
    AssetBuildStateOut,
    AssetChartOut,
    AssetChartPointOut,
    AssetPageOut,
    AssetPointOut,
    JobLinks,
)

router = APIRouter(tags=["assets"], route_class=StrictQueryRoute)

_JOBS_PREFIX = "/api/v1/jobs"
_ENDPOINT_BUILD = "POST /assets/build"
_VALID_STATUSES = frozenset({"ok", "blocked", "degraded"})
_VALID_SORTS = frozenset({"trade_date", "-trade_date"})


def _pct_2dp(ratio_units: int | None) -> str | None:
    """比率标度整数 → 两位小数百分比字符串（避免出现 "-0.00%"）。"""
    if ratio_units is None:
        return None
    text = Ratio(ratio_units).as_pct(2)
    return "0.00%" if text == "-0.00%" else text


def _asset_point_out(dto: Any) -> AssetPointOut:
    """review DTO（标度整数）→ 边界模型（定点字符串 / blocked 显式 null）。"""
    blocked = dto.status == "blocked"
    # blocked 行 DB 数值恒 0、prev/pnl/return 恒 NULL（设计 §4.1 不变式 3），
    # 但对外必须输出**显式 null**（不是 "0.00"、也不是省略键，D6）。
    return AssetPointOut(
        date=dto.trade_date,
        cash=None if blocked else str(Money(dto.cash_units)),
        market_value=None if blocked else str(Money(dto.market_value_units)),
        total_assets=None if blocked else str(Money(dto.total_assets_units)),
        holding_cost=None if blocked else str(Money(dto.holding_cost_units)),
        unrealized_pnl=None if blocked else str(Money(dto.unrealized_pnl_units)),
        holding_count=0 if blocked else dto.holding_count,
        cashflow=None if blocked else str(Money(dto.cashflow_units)),
        prev_total_assets=(
            None
            if blocked or dto.prev_total_assets_units is None
            else str(Money(dto.prev_total_assets_units))
        ),
        daily_pnl=(
            None if blocked or dto.daily_pnl_units is None else str(Money(dto.daily_pnl_units))
        ),
        daily_return=(
            None
            if blocked or dto.daily_return_units is None
            else str(Ratio(dto.daily_return_units))
        ),
        daily_return_pct=None if blocked else _pct_2dp(dto.daily_return_units),
        status=dto.status,
        status_reason=dto.status_reason,
    )


def _build_state_out(dto: Any) -> AssetBuildStateOut:
    return AssetBuildStateOut(
        state=dto.state,
        last_status=dto.last_status,
        last_built_at=dto.last_built_at,
        last_built_through=dto.last_built_through,
        last_mode=dto.last_mode,
        last_rebuild_from=dto.last_rebuild_from,
        last_rows_written=dto.last_rows_written,
        rows_count=dto.rows_count,
        assumption=dto.assumption,
        status_reason=dto.status_reason,
        has_new_market_data=dto.has_new_market_data,
        definition_version=dto.definition_version,
    )


@router.get("/assets", response_model=AssetPageOut, operation_id="listAssets")
async def list_assets_endpoint(
    request: Request,
    ctx: Annotated[TenantContext, Depends(get_tenant)],
    date_from: Annotated[date | None, Query(alias="from")] = None,
    date_to: Annotated[date | None, Query(alias="to")] = None,
    status: Annotated[str | None, Query(description="ok|blocked|degraded")] = None,
    sort: Annotated[str, Query(description="trade_date|-trade_date")] = "-trade_date",
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    cursor: Annotated[str | None, Query()] = None,
) -> AssetPageOut:
    """API-081：每日资产快照列表（日期倒序默认；闭区间过滤；游标分页）。"""
    ctx.forbid_admin_trades()
    user_id = ctx.require_user()
    state = get_state(request)
    market_data = get_market_data(request)
    if status is not None and status not in _VALID_STATUSES:
        raise ApiError(ErrorCode.VALIDATION_ERROR, detail=f"非法的 status 过滤值: {status}")
    if sort not in _VALID_SORTS:
        raise ApiError(ErrorCode.INVALID_SORT_FIELD, detail=f"不支持的排序字段: {sort}")
    if date_from is not None and date_to is not None and date_from > date_to:
        raise ApiError(ErrorCode.INVALID_PERIOD, detail="from 必须不晚于 to")

    from stock_review.application.assets import list_assets

    async with state.db.session(user_id=user_id, begin=True) as session:
        points, next_cursor, build = await list_assets(
            session,
            user_id=user_id,
            clock=state.clock,
            market_data=market_data,
            limit=limit,
            cursor=cursor,
            date_from=date_from,
            date_to=date_to,
            status=status,
            sort=sort,
        )
    build_out = _build_state_out(build)
    return AssetPageOut(
        items=[_asset_point_out(point) for point in points],
        next_cursor=next_cursor,
        has_more=next_cursor is not None,
        build=build_out,
        # 顶层口径版本与 build 块同源（state.definition_version 无 state 时为当前常量）。
        definition_version=build_out.definition_version,
        # 构建血缘：本次构建读取的 market_data.as_of（未构建为 null）。
        data_as_of=build.data_as_of,
    )


def _chart_point_out(point: Any, *, lo: int, hi: int) -> AssetChartPointOut:
    """AssetPoint（标度整数）→ 图表点；归一化只用整数运算，避免 float 业务值。"""
    span = hi - lo
    # 四舍五入到 1bp：((value-lo)*10000 + span/2) // span
    v_bp = 5000 if span <= 0 else ((point.total_assets_units - lo) * 10000 + span // 2) // span
    v = f"{v_bp // 10000}.{v_bp % 10000:04d}"
    return AssetChartPointOut(
        date=point.trade_date,
        v=v,
        label=point.trade_date.strftime("%m-%d"),
        amount_label=str(Money(point.total_assets_units)),
    )


@router.get("/assets/chart", response_model=AssetChartOut, operation_id="getAssetsChart")
async def get_assets_chart_endpoint(
    request: Request,
    ctx: Annotated[TenantContext, Depends(get_tenant)],
    limit: Annotated[int, Query(ge=1, le=500, description="最近 N 个资产日（1~500）")] = 200,
) -> AssetChartOut:
    """API-085：资产日线 chart-ready 视图（只读；blocked 行不进入序列）。"""
    ctx.forbid_admin_trades()
    user_id = ctx.require_user()
    state = get_state(request)
    market_data = get_market_data(request)

    from stock_review.application.assets import list_assets

    async with state.db.session(user_id=user_id, begin=True) as session:
        points, _next_cursor, build = await list_assets(
            session,
            user_id=user_id,
            clock=state.clock,
            market_data=market_data,
            limit=limit,
            cursor=None,
            date_from=None,
            date_to=None,
            status=None,
            sort="-trade_date",
        )

    # 取最近 N 行后反转为时间升序；blocked 行（数值列不可用）不进入序列。
    usable = [point for point in points if point.status != "blocked"]
    usable.reverse()
    if usable:
        values = [point.total_assets_units for point in usable]
        lo, hi = min(values), max(values)
    else:
        lo = hi = 0
    return AssetChartOut(
        points=[_chart_point_out(point, lo=lo, hi=hi) for point in usable],
        n_points=len(usable),
        build_state=build.state,
        definition_version=build.definition_version,
        data_as_of=build.data_as_of,
    )


@router.post(
    "/assets/build",
    status_code=202,
    response_model=AssetBuildAccepted,
    operation_id="buildAssets",
)
async def build_assets_endpoint(
    request: Request,
    response: Response,
    ctx: Annotated[TenantContext, Depends(get_tenant)],
    payload: Annotated[AssetBuildIn, Body()] = AssetBuildIn(),
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> AssetBuildAccepted | JSONResponse:
    """API-082：入队构建/增量更新资产表（202）；单飞复用 / 异参 409。"""
    ctx.forbid_admin_trades()
    user_id = ctx.require_write()
    state = get_state(request)

    if not idempotency_key or not 16 <= len(idempotency_key) <= 128:
        raise ApiError(
            ErrorCode.VALIDATION_ERROR,
            errors=[FieldError("Idempotency-Key", "required", "该操作必须携带幂等键(16~128)")],
        )

    mode: Literal["auto", "full"] = payload.mode
    # request_hash 基于**解析默认值后**的规范 body：缺省 body 与 {"mode":"auto"} 同 key
    # 必须视为同一请求（否则前端省略 body 的重试会误报 422 IDEMPOTENCY_KEY_REUSED）。
    body_json = json.dumps(payload.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    request_hash = hashlib.sha256(body_json.encode("utf-8")).hexdigest()

    async with state.db.session(user_id=user_id, begin=True) as session:
        try:
            record = await state.idempotency.begin(
                session,
                user_id=user_id,
                endpoint=_ENDPOINT_BUILD,
                key=idempotency_key,
                request_hash=request_hash,
            )
        except ApiError as exc:
            if exc.code is ErrorCode.IDEMPOTENCY_IN_PROGRESS:
                job_ref = exc.metadata.get("job_id")
                raise ApiError(
                    ErrorCode.IDEMPOTENCY_IN_PROGRESS,
                    detail="该幂等键正在处理中",
                    headers={"X-Job-Id": str(job_ref)} if job_ref else {},
                ) from exc
            raise

        if record is not None:
            replayed = IdempotencyStore.payload_of(record)
            if replayed is not None:
                return JSONResponse(
                    content=replayed,
                    status_code=IdempotencyStore.status_of(record),
                    headers={
                        "Idempotency-Replayed": "true",
                        "Location": f"{_JOBS_PREFIX}/{replayed.get('job_id')}",
                        "Retry-After": "1",
                    },
                )

        # params 必须是解析默认值后的真实 mode（禁止把 auto 写死，否则 full 会被静默复用）。
        job_id, reused = await state.queue.enqueue(
            session,
            kind="build_assets",
            user_id=user_id,
            scope_key=f"{user_id}:assets",
            params={"mode": mode},
        )
        accepted = AssetBuildAccepted(
            job_id=job_id,
            links=JobLinks(
                self=f"{_JOBS_PREFIX}/{job_id}",
                events=f"{_JOBS_PREFIX}/{job_id}/events",
            ),
        )
        await state.idempotency.complete(
            session,
            user_id=user_id,
            endpoint=_ENDPOINT_BUILD,
            key=idempotency_key,
            status_code=202,
            body=accepted.model_dump(mode="json"),
            job_id=job_id,
        )

    response.headers["Location"] = f"{_JOBS_PREFIX}/{job_id}"
    response.headers["Retry-After"] = "1"
    if reused:
        response.headers["X-Job-Reused"] = "true"
    return accepted


__all__ = ["router"]
