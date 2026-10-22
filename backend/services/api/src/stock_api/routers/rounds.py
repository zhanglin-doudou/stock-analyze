"""回合端点（API-028~031）：重建回合任务、回合列表/详情/FIFO 配对明细。

回合由 worker 的 `rebuild_rounds` 任务落库（review 域）；本 router 只做 HTTP 适配：
* 重建：202 + Location/Retry-After，幂等键必填，同参复用同一 job / 异参 409；
* 查询：读 review.round / round_fill（application 层冻结签名，DTO 标度整数），
  金额/数量/比率在本层转**定点字符串**；跨账号/不存在一律 404，admin 不可读业务数据。

写作用域：本文件只依赖 `stock_review.application.rounds` 的冻结签名（INTERFACE §3.4），
延迟 import 以保证 review 包尚未落盘时 app 仍可装配（endpoint 调用时才需要）。
"""

from __future__ import annotations

import calendar
import hashlib
import json
import re
from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Query, Request, Response
from fastapi.responses import JSONResponse
from stock_contracts.errors import ApiError, ErrorCode, FieldError
from stock_core.values import Money, Qty, Ratio
from stock_platform.idempotency import IdempotencyStore
from stock_platform.tenant import TenantContext

from stock_api.deps import get_state, get_tenant
from stock_api.routing import StrictQueryRoute
from stock_api.schemas import (
    JobAcceptedOut,
    JobLinks,
    RebuildRoundsIn,
    RoundFillOut,
    RoundFillPage,
    RoundOut,
    RoundPage,
)

router = APIRouter(tags=["rounds"], route_class=StrictQueryRoute)

_JOBS_PREFIX = "/api/v1/jobs"
_ENDPOINT_REBUILD = "POST /jobs/rebuild-rounds"
_PERIOD_RE = re.compile(r"^(\d{4})-(\d{2})$")
_VALID_STATUSES = frozenset({"open", "closed", "data_error"})


def _parse_period(period: str) -> tuple[date, date]:
    """YYYY-MM → [当月首日, 当月末日]；非法格式/月份 → 422 INVALID_PERIOD。"""
    matched = _PERIOD_RE.match(period)
    if matched is None:
        raise ApiError(ErrorCode.INVALID_PERIOD, detail=f"period 必须是 YYYY-MM: {period}")
    year, month = int(matched.group(1)), int(matched.group(2))
    if not 1 <= month <= 12:
        raise ApiError(ErrorCode.INVALID_PERIOD, detail=f"period 月份非法: {period}")
    return date(year, month, 1), date(year, month, calendar.monthrange(year, month)[1])


def _round_out(dto: Any) -> RoundOut:
    """review DTO（标度整数）→ 边界模型（定点字符串）。"""
    return RoundOut(
        round_id=dto.id,
        security_code=dto.security_code,
        security_name=dto.security_name,
        status=dto.status,
        open_date=dto.open_date,
        close_date=dto.close_date,
        quantity=str(Qty(dto.quantity_units)),
        cost=str(Money(dto.cost_units)),
        proceeds=str(Money(dto.proceeds_units)),
        dividend=str(Money(dto.dividend_units)),
        pnl=str(Money(dto.pnl_units)),
        return_rate=str(Ratio(dto.return_rate_units)),
        holding_days=dto.holding_days,
        open_trade_id=dto.open_trade_id,
        close_trade_id=dto.close_trade_id,
        data_error=dto.data_error,
        oversell_quantity=str(Qty(dto.oversell_units)),
        fills_count=dto.fills_count,
        rebuilt_at=dto.rebuilt_at,
    )


def _fill_out(dto: Any) -> RoundFillOut:
    return RoundFillOut(
        fill_id=dto.id,
        sequence=dto.sequence,
        buy_trade_id=dto.buy_trade_id,
        sell_trade_id=dto.sell_trade_id,
        quantity=str(Qty(dto.quantity_units)),
        buy_cost=str(Money(dto.buy_cost_units)),
        sell_proceeds=str(Money(dto.sell_proceeds_units)),
        dividend=str(Money(dto.dividend_units)),
        pnl=str(Money(dto.pnl_units)),
        open_date=dto.open_date,
        close_date=dto.close_date,
        holding_days=dto.holding_days,
    )


@router.get("/rounds", response_model=RoundPage, operation_id="listRounds")
async def list_rounds_endpoint(
    request: Request,
    ctx: Annotated[TenantContext, Depends(get_tenant)],
    security_code: Annotated[str | None, Query()] = None,
    status: Annotated[str | None, Query()] = None,
    date_from: Annotated[date | None, Query(alias="from")] = None,
    date_to: Annotated[date | None, Query(alias="to")] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    cursor: Annotated[str | None, Query()] = None,
) -> RoundPage:
    """API-029：回合列表（游标分页/过滤/本人隔离）。"""
    ctx.forbid_admin_trades()
    user_id = ctx.require_user()
    state = get_state(request)
    if status is not None and status not in _VALID_STATUSES:
        raise ApiError(ErrorCode.VALIDATION_ERROR, detail=f"非法的 status 过滤值: {status}")
    if date_from is not None and date_to is not None and date_from > date_to:
        raise ApiError(ErrorCode.INVALID_PERIOD, detail="from 必须不晚于 to")
    from stock_review.application.rounds import list_rounds

    async with state.db.session(user_id=user_id, begin=True) as session:
        dtos, next_cursor = await list_rounds(
            session,
            user_id,
            status=status,
            security_code=security_code,
            date_from=date_from,
            date_to=date_to,
            cursor=cursor,
            limit=limit,
        )
    return RoundPage(
        items=[_round_out(dto) for dto in dtos],
        next_cursor=next_cursor,
        has_more=next_cursor is not None,
    )


@router.get("/rounds/{round_id}", response_model=RoundOut, operation_id="getRound")
async def get_round_endpoint(
    round_id: str,
    request: Request,
    ctx: Annotated[TenantContext, Depends(get_tenant)],
) -> RoundOut:
    """API-030：回合详情；跨账号/不存在 → 404，admin → 403。"""
    ctx.forbid_admin_trades()
    user_id = ctx.require_user()
    state = get_state(request)
    from stock_review.application.rounds import get_round

    async with state.db.session(user_id=user_id, begin=True) as session:
        dto = await get_round(session, user_id, round_id)
    return _round_out(dto)


@router.get("/rounds/{round_id}/fills", response_model=RoundFillPage, operation_id="listRoundFills")
async def list_round_fills_endpoint(
    round_id: str,
    request: Request,
    ctx: Annotated[TenantContext, Depends(get_tenant)],
) -> RoundFillPage:
    """API-031：FIFO 配对明细（一回合内 sequence 从 1 开始）。"""
    ctx.forbid_admin_trades()
    user_id = ctx.require_user()
    state = get_state(request)
    from stock_review.application.rounds import list_round_fills

    async with state.db.session(user_id=user_id, begin=True) as session:
        dtos = await list_round_fills(session, user_id, round_id)
    items = [_fill_out(dto) for dto in dtos]
    return RoundFillPage(items=items, total=len(items))


@router.post(
    "/jobs/rebuild-rounds",
    status_code=202,
    response_model=JobAcceptedOut,
    operation_id="rebuildRounds",
)
async def rebuild_rounds_endpoint(
    payload: RebuildRoundsIn,
    request: Request,
    response: Response,
    ctx: Annotated[TenantContext, Depends(get_tenant)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> JobAcceptedOut | JSONResponse:
    """API-028：入队重建回合（202）；同参复用同一 job，异参单飞 → 409。"""
    ctx.forbid_admin_trades()
    user_id = ctx.require_write()
    state = get_state(request)

    if not idempotency_key or not 16 <= len(idempotency_key) <= 128:
        raise ApiError(
            ErrorCode.VALIDATION_ERROR,
            errors=[FieldError("Idempotency-Key", "required", "该操作必须携带幂等键(16~128)")],
        )

    period = payload.period
    if payload.scope == "period":
        if period is None:
            raise ApiError(
                ErrorCode.INVALID_PERIOD, detail="scope=period 时必须提供 period(YYYY-MM)"
            )
        _parse_period(period)
    elif period is not None:
        raise ApiError(ErrorCode.INVALID_PERIOD, detail="scope=full 时不应提供 period")

    body_json = json.dumps(payload.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    request_hash = hashlib.sha256(body_json.encode("utf-8")).hexdigest()
    params: dict[str, Any] = {
        "scope": payload.scope,
        "period": period,
        "reason": payload.reason,
    }
    # 单飞 scope 与 worker「导入/分类成功后自动 full rebuild」**必须同 key**，
    # 否则手动 full 与自动 full 会并发 delete/insert 互相踩（Lead 集成约定）。
    scope_key = f"{user_id}:full" if payload.scope == "full" else f"{user_id}:period:{period}"

    async with state.db.session(user_id=user_id, begin=True) as session:
        try:
            record = await state.idempotency.begin(
                session,
                user_id=user_id,
                endpoint=_ENDPOINT_REBUILD,
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

        job_id, reused = await state.queue.enqueue(
            session,
            kind="rebuild_rounds",
            user_id=user_id,
            scope_key=scope_key,
            params=params,
        )
        accepted = JobAcceptedOut(
            job_id=job_id,
            type="rebuild_rounds",
            status="pending",
            links=JobLinks(
                self=f"{_JOBS_PREFIX}/{job_id}",
                events=f"{_JOBS_PREFIX}/{job_id}/events",
            ),
        )
        await state.idempotency.complete(
            session,
            user_id=user_id,
            endpoint=_ENDPOINT_REBUILD,
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
