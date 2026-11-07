"""流水端点（API-017~021、080）：游标查询、乐观锁修正、软删/恢复、留痕。

金额/数量/价格一律 **定点字符串**（把 ledger 的标度整数转字符串），禁止 float。
所有查询强制带 user_id；跨账号一律 404；admin 不可读业务明细（§14.2）。
"""

from __future__ import annotations

import hashlib
from datetime import date
from decimal import InvalidOperation
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Header, Query, Request, Response
from fastapi.responses import JSONResponse
from stock_contracts.errors import ApiError, ErrorCode
from stock_core.values import Money, Price, Qty, format_quantity
from stock_ledger.application.trades import (
    TradeDTO,
    TradeFilters,
    delete_trade,
    get_trade,
    history,
    list_trades,
    patch_trade,
    restore_trade,
)
from stock_ledger.parsers.dedupe import dedupe_key as compute_dedupe_key
from stock_ledger.repositories.trades import TradeRepository
from stock_platform.tenant import TenantContext
from stock_review.insights import reasons as reason_service
from stock_review.insights.reasons import ReasonDTO
from stock_review.metrics.datasets import DEFAULT_BENCHMARK, benchmark_fetch_symbol

from stock_api.deps import get_state, get_tenant
from stock_api.routing import StrictQueryRoute
from stock_api.schemas import (
    TradeHistoryItem,
    TradeHistoryOut,
    TradeMutationOut,
    TradeOut,
    TradePage,
    TradePatchIn,
    TradeReasonIn,
    TradeReasonOut,
    TradeRestoreOut,
)

router = APIRouter(tags=["trades"], route_class=StrictQueryRoute)

# API-017 sort 白名单（Catalog §2.1）；非法 → 400 INVALID_SORT_FIELD
_ALLOWED_SORTS: frozenset[str] = frozenset(
    {"trade_date", "-trade_date", "amount", "-amount", "created_at"}
)
# fields 裁剪白名单：TradeOut 字段名 + `id` 别名（§0.4）
_ALLOWED_FIELDS: frozenset[str] = frozenset(TradeOut.model_fields)
_ID_ALIASES = {"id": "trade_id"}


def _reason_out(dto: ReasonDTO | None) -> TradeReasonOut | None:
    if dto is None:
        return None
    return TradeReasonOut(
        trade_id=dto.trade_id,
        side=dto.side,
        reason_code=dto.reason_code,
        reason_label=reason_service.REASON_LABELS.get(dto.reason_code, dto.reason_code),
        reason_text=dto.reason_text,
        emotion_code=dto.emotion_code,
        emotion_label=(
            reason_service.EMOTION_LABELS.get(dto.emotion_code) if dto.emotion_code else None
        ),
        tags=dto.tags,
        annotated_at=dto.annotated_at,
        late_annotated=dto.late_annotated,
        version=dto.version,
    )


def _trade_out(dto: TradeDTO, reason: ReasonDTO | None = None) -> TradeOut:
    """ledger DTO（标度整数）→ API 边界模型（定点字符串）。"""
    return TradeOut(
        trade_id=dto.id,
        trade_date=dto.trade_date,
        security_code=dto.security_code,
        security_name=dto.security_name,
        side=dto.side,
        quantity=format_quantity(dto.quantity_units),
        price=str(Price(dto.price_units)),
        amount=str(Money(dto.amount_units)),
        commission=str(Money(dto.commission_units)),
        stamp_tax=str(Money(dto.stamp_tax_units)),
        other_fee=str(Money(dto.other_fee_units)),
        cash_balance=(
            str(Money(dto.cash_balance_units)) if dto.cash_balance_units is not None else None
        ),
        cash_balance_available=dto.cash_balance_available,
        money_fund_balance=(
            str(Money(dto.money_fund_balance_units))
            if dto.money_fund_balance_units is not None
            else None
        ),
        account_masked=dto.account_masked,
        summary=dto.summary,
        summary_code=dto.summary_code,
        category=dto.category,
        dedupe_key=compute_dedupe_key(dto),
        version=dto.version,
        source=dto.source,
        batch_id=dto.batch_id,
        note=dto.note,
        deleted_at=dto.deleted_at,
        created_at=dto.created_at,
        updated_at=dto.updated_at,
        reason=_reason_out(reason),
    )


def _etag_with_reasons(dtos: list[TradeDTO], reason_map: dict[str, ReasonDTO]) -> str:
    """列表 ETag：行 `id:version:reason_version` 顺序哈希（含理由版本，additive）。"""
    raw = "|".join(
        f"{dto.id}:{dto.version}:{reason_map[compute_dedupe_key(dto)].version if compute_dedupe_key(dto) in reason_map else 0}"
        for dto in dtos
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _if_none_match_hit(header_value: str | None, etag: str) -> bool:
    """条件 GET：忽略 `W/` 弱校验前缀与引号后逐字比较。"""
    if not header_value:
        return False
    candidate = header_value.strip()
    if candidate.startswith("W/"):
        candidate = candidate[2:]
    return candidate.strip('"') == etag


def _parse_units(raw: str | None, cls: type[Money] | type[Price] | type[Qty], field: str) -> int:
    """定点字符串 → 标度整数；非法一律 422。"""
    if raw is None:
        raise ApiError(ErrorCode.VALIDATION_ERROR, detail=f"{field} 不能为空")
    try:
        return cls.from_str(raw).units
    except (ValueError, InvalidOperation) as exc:
        raise ApiError(ErrorCode.VALIDATION_ERROR, detail=f"{field} 不是合法的定点数") from exc


def _patch_changes(payload: TradePatchIn) -> dict[str, Any]:
    """TradePatchIn → 仓储可变字段（标度整数）；未提供字段不动。"""
    provided = payload.model_dump(exclude_unset=True)
    changes: dict[str, Any] = {}
    for name, value in provided.items():
        if name == "version":
            continue
        if name in {"trade_date", "summary_code", "note"}:
            changes[name] = value
        elif name == "quantity":
            changes["quantity_units"] = _parse_units(value, Qty, name)
        elif name == "price":
            changes["price_units"] = _parse_units(value, Price, name)
        elif name in {"amount", "commission", "stamp_tax", "other_fee"}:
            changes[f"{name}_units"] = _parse_units(value, Money, name)
        elif name == "cash_balance":
            changes["cash_balance_units"] = (
                None if value is None else _parse_units(value, Money, name)
            )
        elif name == "money_fund_balance":
            changes["money_fund_balance_units"] = (
                None if value is None else _parse_units(value, Money, name)
            )
    return changes


def _resolve_if_match(if_match: str | None, body_version: int | None) -> int:
    """乐观锁版本：If-Match（`"<version>"`）优先，body.version 兜底（§0.3）。"""
    if if_match is not None:
        try:
            return int(if_match.strip().strip('"'))
        except ValueError as exc:
            raise ApiError(ErrorCode.VALIDATION_ERROR, detail="If-Match 必须是版本号") from exc
    if body_version is None:
        raise ApiError(ErrorCode.VALIDATION_ERROR, detail="缺少 If-Match 或 version")
    return body_version


@router.get("/trades", response_model=TradePage, operation_id="listTrades")
async def list_trades_endpoint(
    request: Request,
    response: Response,
    ctx: Annotated[TenantContext, Depends(get_tenant)],
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    cursor: Annotated[str | None, Query()] = None,
    sort: Annotated[str | None, Query()] = "-trade_date",
    trade_date_from: Annotated[date | None, Query()] = None,
    trade_date_to: Annotated[date | None, Query()] = None,
    security_code: Annotated[str | None, Query()] = None,
    side: Annotated[Literal["buy", "sell", "unknown", "missing"] | None, Query()] = None,
    batch_id: Annotated[str | None, Query()] = None,
    include_deleted: Annotated[bool, Query()] = False,
    fields: Annotated[str | None, Query()] = None,
    include_total: Annotated[bool, Query()] = False,
    has_reason: Annotated[bool | None, Query()] = None,
    reason_code: Annotated[str | None, Query(max_length=24)] = None,
) -> TradePage | Response:
    """API-017：游标分页 + 字段裁剪 + ETag/If-None-Match（304 空 body）。"""
    ctx.forbid_admin_trades()
    user_id = ctx.require_user()
    state = get_state(request)

    if sort is not None and sort not in _ALLOWED_SORTS:
        raise ApiError(ErrorCode.INVALID_SORT_FIELD, detail=f"不支持的排序字段: {sort}")

    selected: list[tuple[str, str]] | None = None
    if fields is not None:
        selected = []
        for raw_name in (item.strip() for item in fields.split(",")):
            if not raw_name:
                continue
            canonical = _ID_ALIASES.get(raw_name, raw_name)
            if canonical not in _ALLOWED_FIELDS:
                raise ApiError(ErrorCode.INVALID_FIELD_SELECTION, detail=f"未知字段: {raw_name}")
            selected.append((raw_name, canonical))

    filters = TradeFilters(
        security_code=security_code,
        side=side,
        date_from=trade_date_from,
        date_to=trade_date_to,
        include_deleted=include_deleted,
    )
    async with state.db.session(user_id=user_id, begin=True) as session:
        if has_reason is not None or reason_code is not None:
            keys = await reason_service.matching_dedupe_keys(
                session, user_id, reason_code=reason_code
            )
            if has_reason is False:
                filters.exclude_dedupe_keys = tuple(keys)
            elif keys:
                filters.dedupe_keys = tuple(keys)
            else:
                # has_reason=True / reason_code 过滤但无匹配：直接空页
                filters.dedupe_keys = ("__none__",)
        dtos, next_cursor, _ = await list_trades(
            session, user_id=user_id, cursor=cursor, limit=limit, filters=filters
        )
        reason_map: dict[str, ReasonDTO] = {}
        if dtos:
            rows = await reason_service.active_by_dedupe(
                session, user_id, [compute_dedupe_key(dto) for dto in dtos]
            )
            reason_map = {key: ReasonDTO.from_row(row) for key, row in rows.items()}

    etag = _etag_with_reasons(dtos, reason_map)
    etag_header = f'"{etag}"'
    if _if_none_match_hit(request.headers.get("if-none-match"), etag):
        return Response(status_code=304, headers={"ETag": etag_header})

    has_more = next_cursor is not None
    if selected is not None:
        projected = [
            {
                key: _trade_out(dto, reason_map.get(compute_dedupe_key(dto))).model_dump(
                    mode="json"
                )[key]
                for _, key in selected
            }
            for dto in dtos
        ]
        return JSONResponse(
            content={"items": projected, "next_cursor": next_cursor, "has_more": has_more},
            headers={"ETag": etag_header},
        )

    response.headers["ETag"] = etag_header
    return TradePage(
        items=[_trade_out(dto, reason_map.get(compute_dedupe_key(dto))) for dto in dtos],
        next_cursor=next_cursor,
        has_more=has_more,
    )


@router.get("/trades/{trade_id}", response_model=TradeOut, operation_id="getTrade")
async def get_trade_detail(
    trade_id: str,
    request: Request,
    response: Response,
    ctx: Annotated[TenantContext, Depends(get_tenant)],
    include_deleted: Annotated[bool, Query()] = False,
) -> TradeOut:
    """API-080：流水详情（完整行，含 lineage/version）。"""
    ctx.forbid_admin_trades()
    user_id = ctx.require_user()
    state = get_state(request)
    async with state.db.session(user_id=user_id, begin=True) as session:
        dto = await get_trade(
            session, user_id=user_id, trade_id=trade_id, include_deleted=include_deleted
        )
        rows = await reason_service.active_by_dedupe(session, user_id, [compute_dedupe_key(dto)])
        reason = ReasonDTO.from_row(rows[compute_dedupe_key(dto)]) if rows else None
    reason_version = reason.version if reason is not None else 0
    response.headers["ETag"] = f'"{dto.version}:{reason_version}"'
    return _trade_out(dto, reason)


@router.patch("/trades/{trade_id}", response_model=TradeMutationOut, operation_id="patchTrade")
async def patch_trade_endpoint(
    trade_id: str,
    payload: TradePatchIn,
    request: Request,
    response: Response,
    ctx: Annotated[TenantContext, Depends(get_tenant)],
    if_match: Annotated[str | None, Header(alias="If-Match")] = None,
) -> TradeMutationOut:
    """API-018：单行修正（If-Match 乐观锁）；版本冲突 → 409。"""
    ctx.forbid_admin_trades()
    user_id = ctx.require_write()
    state = get_state(request)
    expected_version = _resolve_if_match(if_match, payload.version)
    changes = _patch_changes(payload)
    if not changes:
        raise ApiError(ErrorCode.VALIDATION_ERROR, detail="没有可修改的字段")
    async with state.db.session(user_id=user_id, begin=True) as session:
        dto = await patch_trade(
            session,
            user_id=user_id,
            trade_id=trade_id,
            changes=changes,
            expected_version=expected_version,
            clock=state.clock,
            actor_id=user_id,
            request_id=request.headers.get("x-request-id"),
        )
        rows = await reason_service.active_by_dedupe(session, user_id, [compute_dedupe_key(dto)])
        reason = ReasonDTO.from_row(rows[compute_dedupe_key(dto)]) if rows else None
    response.headers["ETag"] = f'"{dto.version}:{reason.version if reason else 0}"'
    return TradeMutationOut(trade=_trade_out(dto, reason))


@router.delete("/trades/{trade_id}", status_code=204, operation_id="deleteTrade")
async def delete_trade_endpoint(
    trade_id: str,
    request: Request,
    ctx: Annotated[TenantContext, Depends(get_tenant)],
) -> Response:
    """API-019：软删单行（204，幂等：已删除行重复调用无副作用）。"""
    ctx.forbid_admin_trades()
    user_id = ctx.require_write()
    state = get_state(request)
    async with state.db.session(user_id=user_id, begin=True) as session:
        dto = await get_trade(session, user_id=user_id, trade_id=trade_id, include_deleted=True)
        if dto.deleted_at is None:
            await delete_trade(
                session,
                user_id=user_id,
                trade_id=trade_id,
                clock=state.clock,
                actor_id=user_id,
                request_id=request.headers.get("x-request-id"),
            )
    return Response(status_code=204)


@router.post(
    "/trades/{trade_id}/restore",
    response_model=TradeRestoreOut,
    operation_id="restoreTrade",
)
async def restore_trade_endpoint(
    trade_id: str,
    request: Request,
    response: Response,
    ctx: Annotated[TenantContext, Depends(get_tenant)],
) -> TradeRestoreOut:
    """API-020：恢复软删行；未删除行 → 422，重复恢复 → 200 状态不变。"""
    ctx.forbid_admin_trades()
    user_id = ctx.require_write()
    state = get_state(request)
    async with state.db.session(user_id=user_id, begin=True) as session:
        dto = await get_trade(session, user_id=user_id, trade_id=trade_id, include_deleted=True)
        if dto.deleted_at is None:
            rows = await history(session, user_id=user_id, trade_id=trade_id)
            if not any(item.op == "restore" for item in rows):
                raise ApiError(ErrorCode.VALIDATION_ERROR, detail="该行未被删除，无需恢复")
        else:
            dto = await restore_trade(
                session,
                user_id=user_id,
                trade_id=trade_id,
                clock=state.clock,
                actor_id=user_id,
                request_id=request.headers.get("x-request-id"),
            )
    response.headers["ETag"] = f'"{dto.version}"'
    return TradeRestoreOut(trade=_trade_out(dto))


@router.get(
    "/trades/{trade_id}/history",
    response_model=TradeHistoryOut,
    operation_id="getTradeHistory",
)
async def trade_history(
    trade_id: str,
    request: Request,
    ctx: Annotated[TenantContext, Depends(get_tenant)],
) -> TradeHistoryOut:
    """API-021：变更留痕（create/update/delete/restore，含版本与快照）。"""
    ctx.forbid_admin_trades()
    user_id = ctx.require_user()
    state = get_state(request)
    async with state.db.session(user_id=user_id, begin=True) as session:
        # 归属校验：不存在 → 404（跨账号同样按不存在处理）
        await get_trade(session, user_id=user_id, trade_id=trade_id, include_deleted=True)
        rows = await history(session, user_id=user_id, trade_id=trade_id)
    items = [
        TradeHistoryItem(
            version=row.version,
            op=row.op,
            changed_fields=sorted(row.changed_fields.keys()),
            snapshot=row.snapshot,
            actor_id=row.actor_id,
            at=row.at,
        )
        for row in rows
    ]
    return TradeHistoryOut(trade_id=trade_id, items=items)


def _optional_if_match(if_match: str | None) -> int | None:
    if if_match is None:
        return None
    try:
        return int(if_match.strip().strip('"'))
    except ValueError as exc:
        raise ApiError(ErrorCode.VALIDATION_ERROR, detail="If-Match 必须是版本号") from exc


async def _reason_calendar(state: Any, trade_date: date) -> tuple[date, ...]:
    """取基准日历（用于服务端 late_annotated 计算）；失败时降级为空日历。"""
    from datetime import timedelta

    bench_symbol = benchmark_fetch_symbol(DEFAULT_BENCHMARK, state.market_data)
    start = trade_date - timedelta(days=60)
    end = state.clock.today()
    bars = await state.market_data.get_daily_bars([bench_symbol], start, end)
    return tuple(sorted({bar.trade_date for bar in bars}))


@router.put(
    "/trades/{trade_id}/reason",
    response_model=TradeReasonOut,
    operation_id="upsertTradeReason",
)
async def put_trade_reason(
    trade_id: str,
    payload: TradeReasonIn,
    request: Request,
    response: Response,
    ctx: Annotated[TenantContext, Depends(get_tenant)],
    if_match: Annotated[str | None, Header(alias="If-Match")] = None,
) -> TradeReasonOut:
    """API-090：新增/更新逐笔理由（新建 201、更新 200；版本冲突 409）。"""
    ctx.forbid_admin_trades()
    user_id = ctx.require_write()
    state = get_state(request)
    expected_version = _optional_if_match(if_match)
    async with state.db.session(user_id=user_id, begin=True) as session:
        trade = await TradeRepository(session).by_id(user_id, trade_id, include_deleted=True)
        if trade is None or trade.deleted_at is not None or trade.side not in {"buy", "sell"}:
            # 设计 §6.1：不存在/跨用户/非 buy|sell/已删除 一律 404，不泄露资源状态。
            raise ApiError(ErrorCode.NOT_FOUND, detail=f"交易 {trade_id} 不存在")
        calendar = await _reason_calendar(state, trade.trade_date)
        dto, created = await reason_service.put_reason(
            session,
            user_id,
            trade=trade,
            reason_code=payload.reason_code,
            reason_text=payload.reason_text,
            emotion_code=payload.emotion_code,
            tags=payload.tags,
            expected_version=expected_version,
            clock=state.clock,
            calendar=calendar,
            request_id=request.headers.get("x-request-id"),
        )
    if created:
        response.status_code = 201
    response.headers["ETag"] = f'"{dto.version}"'
    out = _reason_out(dto)
    assert out is not None
    return out


@router.delete(
    "/trades/{trade_id}/reason",
    status_code=204,
    operation_id="deleteTradeReason",
)
async def delete_trade_reason(
    trade_id: str,
    request: Request,
    ctx: Annotated[TenantContext, Depends(get_tenant)],
    if_match: Annotated[str | None, Header(alias="If-Match")] = None,
) -> Response:
    """API-091：软删理由（204 幂等）；不存在同账号返回 204，跨账号 404。"""
    ctx.forbid_admin_trades()
    user_id = ctx.require_write()
    state = get_state(request)
    expected_version = _optional_if_match(if_match)
    async with state.db.session(user_id=user_id, begin=True) as session:
        trade = await TradeRepository(session).by_id(user_id, trade_id, include_deleted=True)
        if trade is None or trade.side not in {"buy", "sell"}:
            raise ApiError(ErrorCode.NOT_FOUND, detail=f"交易 {trade_id} 不存在")
        await reason_service.delete_reason(
            session,
            user_id,
            trade=trade,
            expected_version=expected_version,
            clock=state.clock,
            request_id=request.headers.get("x-request-id"),
        )
    return Response(status_code=204)
