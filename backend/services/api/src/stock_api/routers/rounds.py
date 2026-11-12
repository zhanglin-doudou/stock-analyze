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
from sqlalchemy import select
from stock_contracts.enums import Role
from stock_contracts.errors import ApiError, ErrorCode, FieldError
from stock_core.trade_flags import LOOKAHEAD_LABEL_ZH, STATUS_LABELS, TRADE_FLAG_BY_CODE
from stock_core.values import Money, Ratio, format_quantity
from stock_platform.audit import record as audit_record
from stock_platform.idempotency import IdempotencyStore
from stock_platform.tenant import TenantContext

from stock_api.deps import get_state, get_tenant
from stock_api.routing import StrictQueryRoute
from stock_api.schemas import (
    FlagGlossaryOut,
    ForwardReturnOut,
    InsightAddonSummaryOut,
    InsightBucketSummaryOut,
    InsightFillSwingOut,
    InsightPnlHoldingClassOut,
    InsightPnlHoldingOut,
    InsightRankingOut,
    InsightReasonCoverageOut,
    InsightReasonShareOut,
    InsightSellFlySummaryOut,
    InsightsSummaryOut,
    JobAcceptedOut,
    JobLinks,
    RebuildRoundsIn,
    RoundFillOut,
    RoundFillPage,
    RoundInsightDetailOut,
    RoundInsightItemOut,
    RoundInsightsPageOut,
    RoundInsightsRefreshIn,
    RoundInsightsRefreshOut,
    RoundOut,
    RoundPage,
    TradeFlagGlossaryOut,
    TradeFlagOut,
    TradeSideInsightOut,
)

router = APIRouter(tags=["rounds"], route_class=StrictQueryRoute)

_JOBS_PREFIX = "/api/v1/jobs"
_ENDPOINT_REBUILD = "POST /jobs/rebuild-rounds"
_ENDPOINT_INSIGHTS_REFRESH = "POST /rounds/insights/refresh"
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
        quantity=format_quantity(dto.quantity_units),
        cost=str(Money(dto.cost_units)),
        proceeds=str(Money(dto.proceeds_units)),
        dividend=str(Money(dto.dividend_units)),
        pnl=str(Money(dto.pnl_units)),
        return_rate=str(Ratio(dto.return_rate_units)),
        holding_days=dto.holding_days,
        open_trade_id=dto.open_trade_id,
        close_trade_id=dto.close_trade_id,
        data_error=dto.data_error,
        oversell_quantity=format_quantity(dto.oversell_units),
        fills_count=dto.fills_count,
        rebuilt_at=dto.rebuilt_at,
    )


def _fill_out(dto: Any) -> RoundFillOut:
    return RoundFillOut(
        fill_id=dto.id,
        sequence=dto.sequence,
        buy_trade_id=dto.buy_trade_id,
        sell_trade_id=dto.sell_trade_id,
        quantity=format_quantity(dto.quantity_units),
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


def _glossary_out(entries: list[Any]) -> list[FlagGlossaryOut]:
    """旗标词表原样透出（文案由 core 单点维护，本层不做拼装/翻译）。"""
    return [
        FlagGlossaryOut(
            code=entry.code,
            label_zh=entry.label_zh,
            label_en=entry.label_en,
            description_zh=entry.description_zh,
            description_en=entry.description_en,
            kind=entry.kind,
        )
        for entry in entries
    ]


def _forward_out(view: Any) -> ForwardReturnOut:
    return ForwardReturnOut(
        horizon=view.horizon,
        status=view.status,
        base_shifted=view.base_shifted,
        b0_pct=view.b0_pct,
        b0_net_pct=view.b0_net_pct,
        b1_pct=view.b1_pct,
        b2_pct=view.b2_pct,
        b3_pct=view.b3_pct,
        max_close_display=view.max_close_display,
        min_close_display=view.min_close_display,
        price_jump_review=view.price_jump_review,
    )


def _trade_flags_out(records: Any) -> list[TradeFlagOut]:
    out: list[TradeFlagOut] = []
    for record in records:
        definition = TRADE_FLAG_BY_CODE[record.code]
        out.append(
            TradeFlagOut(
                code=record.code,
                label_zh=definition.label_zh,
                label_en=definition.label_en,
                status=record.status,
                status_label=STATUS_LABELS.get(record.status, record.status),
                lookahead=record.lookahead,
                lookahead_label=(LOOKAHEAD_LABEL_ZH if record.lookahead else None),
                reason=record.reason,
            )
        )
    return out


def _trade_flag_glossary_out(entries: Any) -> list[TradeFlagGlossaryOut]:
    return [
        TradeFlagGlossaryOut(
            code=entry.code,
            label_zh=entry.label_zh,
            label_en=entry.label_en,
            description_zh=entry.description_zh,
            description_en=entry.description_en,
            side=entry.side,
            lookahead=entry.lookahead,
        )
        for entry in entries
    ]


def _side_out(view: Any) -> TradeSideInsightOut | None:
    if view is None:
        return None
    return TradeSideInsightOut(
        trade_id=view.trade_id,
        date=view.date,
        price_display=view.price_display,
        quantity_display=view.quantity_display,
        amount_display=view.amount_display,
        reason=_trade_reason_out(view.reason),
        reason_label=view.reason_label,
        forward={str(horizon): _forward_out(item) for horizon, item in view.forward.items()},
        trade_flags=_trade_flags_out(view.trade_flags),
    )


def _trade_reason_out(dto: Any) -> Any:
    if dto is None:
        return None
    from stock_api.routers.trades import _reason_out

    return _reason_out(dto)


def _item_out(item: Any) -> RoundInsightItemOut:
    return RoundInsightItemOut(
        round_id=item.round_id,
        security_code=item.security_code,
        security_name=item.security_name,
        status=item.status,
        open_date=item.open_date,
        close_date=item.close_date,
        holding_days_legacy=item.holding_days_legacy,
        holding_trading_days=item.holding_trading_days,
        bucket=item.bucket,
        pnl=str(Money(item.pnl_units)),
        return_rate_pct=Ratio(item.return_rate_units).as_pct(2),
        buy_trade=_side_out(item.buy_trade),
        sell_trade=_side_out(item.sell_trade),
        buy_excess_pct=_pct_units(item.buy_excess_units),
        sell_avoid_pct=_pct_units(item.sell_avoid_units),
        combined_pct=_pct_units(item.combined_units),
        rank=item.rank,
        quadrant=item.quadrant,
        flags=item.flags,
        quality=list(dict.fromkeys(item.quality)),
        missed_profit_worst_display=(
            str(Money(item.missed_profit_worst_units))
            if item.missed_profit_worst_units is not None
            else None
        ),
        missed_profit_end_display=(
            str(Money(item.missed_profit_end_units))
            if item.missed_profit_end_units is not None
            else None
        ),
        missed_profit_worst_abs_display=item.missed_profit_worst_abs_display,
        missed_profit_worst_label=item.missed_profit_worst_label,
        missed_profit_worst_label_en=item.missed_profit_worst_label_en,
        missed_profit_end_abs_display=item.missed_profit_end_abs_display,
        missed_profit_end_label=item.missed_profit_end_label,
        missed_profit_end_label_en=item.missed_profit_end_label_en,
    )


def _pct_units(units: int | None) -> str | None:
    return None if units is None else Ratio(units).as_pct(2)


def _summary_out(summary: Any) -> InsightsSummaryOut:
    swing = summary.swing
    return InsightsSummaryOut(
        swing=InsightBucketSummaryOut(
            closed=swing.closed,
            scalp=swing.scalp,
            short=swing.short,
            position=swing.position,
            long=swing.long,
            avg_holding_trading_days=swing.avg_holding_trading_days,
            win_rate_pct=swing.win_rate_pct,
            avg_pnl_display=swing.avg_pnl_display,
            total_pnl_display=swing.total_pnl_display,
        ),
        addon=InsightAddonSummaryOut(
            count=summary.addon.count,
            declined_after_addon=summary.addon.declined_after_addon,
            declined_pct=summary.addon.declined_pct,
            avg_dip_pct=summary.addon.avg_dip_pct,
            avg_dip_coverage=summary.addon.avg_dip_coverage,
            rounds=summary.addon.rounds,
            share_pct=summary.addon.share_pct,
        ),
        sell_fly=InsightSellFlySummaryOut(
            sell_count=summary.sell_fly.sell_count,
            profit_take_fly=summary.sell_fly.profit_take_fly,
            stop_loss_rebound=summary.sell_fly.stop_loss_rebound,
            fly_pct=summary.sell_fly.fly_pct,
            missed_profit_worst_display=summary.sell_fly.missed_profit_worst_display,
            missed_profit_end_display=summary.sell_fly.missed_profit_end_display,
            missed_profit_worst_abs_display=summary.sell_fly.missed_profit_worst_abs_display,
            missed_profit_worst_label=summary.sell_fly.missed_profit_worst_label,
            missed_profit_worst_label_en=summary.sell_fly.missed_profit_worst_label_en,
            missed_profit_end_abs_display=summary.sell_fly.missed_profit_end_abs_display,
            missed_profit_end_label=summary.sell_fly.missed_profit_end_label,
            missed_profit_end_label_en=summary.sell_fly.missed_profit_end_label_en,
            rounds=summary.sell_fly.rounds,
            share_pct=summary.sell_fly.share_pct,
        ),
        fill_swing=InsightFillSwingOut(
            pairs=summary.fill_swing.pairs,
            win_rate_pct=summary.fill_swing.win_rate_pct,
            avg_holding_trading_days=summary.fill_swing.avg_holding_trading_days,
            avg_pnl_display=summary.fill_swing.avg_pnl_display,
            total_pnl_display=summary.fill_swing.total_pnl_display,
        ),
        holding_by_pnl=InsightPnlHoldingOut(
            flat_tolerance_display=summary.holding_by_pnl.flat_tolerance_display,
            classes=[
                InsightPnlHoldingClassOut(
                    code=row.code,
                    label=row.label,
                    count=row.count,
                    avg_holding_trading_days=row.avg_holding_trading_days,
                    avg_holding_trading_days_x10=row.avg_holding_trading_days_x10,
                )
                for row in summary.holding_by_pnl.classes
            ],
        ),
        reason_coverage=InsightReasonCoverageOut(
            annotated=summary.reason_coverage.annotated,
            total_rounds=summary.reason_coverage.total_rounds,
            coverage_pct=summary.reason_coverage.coverage_pct,
            by_code=[
                InsightReasonShareOut(
                    code=row.code,
                    label=row.label,
                    count=row.count,
                    share_pct=row.share_pct,
                )
                for row in summary.reason_coverage.by_code
            ],
        ),
        ranking=InsightRankingOut(
            n_sample=summary.ranking.n_sample,
            n_eligible=summary.ranking.n_eligible,
            sample_status=summary.ranking.sample_status,
            combined_median_pct=summary.ranking.combined_median_pct,
            combined_avg_pct=summary.ranking.combined_avg_pct,
            quadrant_counts=summary.ranking.quadrant_counts,
            per_stock=summary.ranking.per_stock,
            basis=summary.ranking.basis,
            definition_version=summary.ranking.definition_version,
        ),
        pending=summary.pending,
    )


@router.get(
    "/rounds/insights",
    response_model=RoundInsightsPageOut,
    operation_id="listRoundInsights",
)
async def list_round_insights_endpoint(
    request: Request,
    ctx: Annotated[TenantContext, Depends(get_tenant)],
    period: Annotated[str | None, Query()] = None,
    bucket: Annotated[str | None, Query()] = None,
    flag: Annotated[list[str] | None, Query()] = None,
    has_reason: Annotated[bool | None, Query()] = None,
    reason_code: Annotated[str | None, Query(max_length=24)] = None,
    security_code: Annotated[str | None, Query()] = None,
    security_type: Annotated[list[str] | None, Query()] = None,
    sort: Annotated[str, Query()] = "combined",
    sort_dir: Annotated[str, Query()] = "desc",
    include_summary: Annotated[bool, Query()] = True,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    cursor: Annotated[str | None, Query()] = None,
) -> RoundInsightsPageOut:
    """API-092：回合洞察列表（静态路由必须先于 `/rounds/{round_id}`）。"""
    ctx.forbid_admin_trades()
    user_id = ctx.require_user()
    state = get_state(request)
    from stock_review.insights.service import get_insights_page

    flags: list[str] = []
    for raw in flag or []:
        flags.extend(part.strip() for part in raw.split(",") if part.strip())
    security_types: list[str] = []
    for raw in security_type or []:
        security_types.extend(part.strip() for part in raw.split(",") if part.strip())
    async with state.db.session(user_id=user_id, begin=True) as session:
        page = await get_insights_page(
            session,
            user_id,
            market_data=state.market_data,
            clock=state.clock,
            period=period,
            bucket=bucket,
            flags=flags,
            has_reason=has_reason,
            reason_code=reason_code,
            security_code=security_code,
            security_types=security_types,
            sort=sort,
            sort_dir=sort_dir,
            include_summary=include_summary,
            limit=limit,
            cursor=cursor,
        )
    return RoundInsightsPageOut(
        period=page.period,
        period_basis=page.period_basis,
        definition_version=page.definition_version,
        bucket_basis=page.bucket_basis,
        data_as_of=page.data_as_of,
        ranking_as_of=page.ranking_as_of,
        benchmark_id=page.benchmark_id,
        basis=page.basis,
        item_total=page.item_total,
        summary=_summary_out(page.summary) if page.summary is not None else None,
        items=[_item_out(item) for item in page.items],
        next_cursor=page.next_cursor,
        has_more=page.has_more,
        missing=page.missing,
        assumptions=page.assumptions,
        disclaimer=page.disclaimer,
        flag_glossary=_glossary_out(page.flag_glossary),
    )


@router.get(
    "/rounds/{round_id}/insights",
    response_model=RoundInsightDetailOut,
    operation_id="getRoundInsight",
)
async def get_round_insights_endpoint(
    round_id: str,
    request: Request,
    ctx: Annotated[TenantContext, Depends(get_tenant)],
) -> RoundInsightDetailOut:
    """API-093：单回合洞察详情（回合基础 + 配对明细 + 每笔理由/前瞻）。"""
    ctx.forbid_admin_trades()
    user_id = ctx.require_user()
    state = get_state(request)
    from stock_review.insights.service import get_round_insight

    async with state.db.session(user_id=user_id, begin=True) as session:
        detail = await get_round_insight(
            session,
            user_id,
            round_id=round_id,
            market_data=state.market_data,
            clock=state.clock,
        )
    fills = [
        {
            **{key: value for key, value in fill.items() if key not in {"buy", "sell"}},
            "buy": _side_out(fill["buy"]),
            "sell": _side_out(fill["sell"]),
        }
        for fill in detail.fills
    ]
    return RoundInsightDetailOut(
        round_id=detail.round_id,
        security_code=detail.security_code,
        security_name=detail.security_name,
        status=detail.status,
        open_date=detail.open_date,
        close_date=detail.close_date,
        holding_days_legacy=detail.holding_days_legacy,
        holding_trading_days=detail.holding_trading_days,
        bucket=detail.bucket,
        pnl=str(Money(detail.pnl_units)),
        return_rate_pct=Ratio(detail.return_rate_units).as_pct(2),
        buy_trade=_side_out(detail.buy_trade),
        sell_trade=_side_out(detail.sell_trade),
        fills=fills,
        flags=detail.flags,
        quality=list(dict.fromkeys(detail.quality)),
        ranking=detail.ranking,
        disclaimer=detail.disclaimer,
        flag_glossary=_glossary_out(detail.flag_glossary),
        trade_flag_glossary=_trade_flag_glossary_out(detail.trade_flag_glossary),
    )


@router.post(
    "/rounds/insights/refresh",
    response_model=RoundInsightsRefreshOut,
    operation_id="refreshRoundInsights",
    responses={
        202: {
            "model": JobAcceptedOut,
            "description": "预计处理超过 5000 笔，转后台作业（Location: /jobs/{id}）",
        }
    },
)
async def refresh_round_insights_endpoint(
    payload: RoundInsightsRefreshIn,
    request: Request,
    response: Response,
    ctx: Annotated[TenantContext, Depends(get_tenant)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> RoundInsightsRefreshOut | JobAcceptedOut | JSONResponse:
    """API-094：重算前瞻收益缓存；>5000 笔转 202 作业。"""
    ctx.forbid_admin_trades()
    user_id = ctx.require_write()
    state = get_state(request)
    if not idempotency_key or not 16 <= len(idempotency_key) <= 128:
        raise ApiError(
            ErrorCode.VALIDATION_ERROR,
            errors=[FieldError("Idempotency-Key", "required", "该操作必须携带幂等键(16~128)")],
        )
    if payload.scope == "all" and ctx.role is not Role.ADMIN:
        raise ApiError(ErrorCode.FORBIDDEN, detail="scope=all 仅管理员可执行")

    from sqlalchemy import func
    from stock_ledger.models import Trade
    from stock_review.insights.service import _resolve_period, refresh_forwards

    resolved = _resolve_period(payload.period, state.clock)
    async with state.db.session(user_id=user_id, begin=True) as session:
        count = await session.scalar(
            select(func.count())
            .select_from(Trade)
            .where(
                Trade.user_id == user_id,
                Trade.deleted_at.is_(None),
                Trade.trade_date >= resolved.start,
                Trade.trade_date <= resolved.end,
            )
        )
    if (count or 0) > 5000:
        scope_key = f"round_insights:{user_id}"
        body_json = json.dumps(
            payload.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
        )
        request_hash = hashlib.sha256(body_json.encode("utf-8")).hexdigest()
        async with state.db.session(user_id=user_id, begin=True) as session:
            record = await state.idempotency.begin(
                session,
                user_id=user_id,
                endpoint=_ENDPOINT_INSIGHTS_REFRESH,
                key=idempotency_key,
                request_hash=request_hash,
            )
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
                kind="refresh_round_insights",
                user_id=user_id,
                scope_key=scope_key,
                params={"period": resolved.label, "scope": payload.scope},
            )
            accepted = JobAcceptedOut(
                job_id=job_id,
                type="refresh_round_insights",
                status="pending",
                links=JobLinks(
                    self=f"{_JOBS_PREFIX}/{job_id}",
                    events=f"{_JOBS_PREFIX}/{job_id}/events",
                ),
            )
            await state.idempotency.complete(
                session,
                user_id=user_id,
                endpoint=_ENDPOINT_INSIGHTS_REFRESH,
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

    async with state.db.session(user_id=user_id, begin=True) as session:
        result = await refresh_forwards(
            session,
            user_id,
            market_data=state.market_data,
            clock=state.clock,
            period=payload.period,
            scope=payload.scope,
        )
        await audit_record(
            session,
            action="round_insights.refresh",
            actor_id=user_id,
            target_type="round_insights",
            target_id=str(user_id),
            meta={
                "computed": result.computed,
                "skipped": result.skipped,
                "period": resolved.label,
            },
            request_id=request.headers.get("x-request-id"),
        )
    return RoundInsightsRefreshOut(
        computed=result.computed,
        skipped=result.skipped,
        pending=result.pending,
        no_calendar=result.no_calendar,
        no_bar=result.no_bar,
        corporate_action=result.corporate_action,
        divisor_missing=result.divisor_missing,
        stale_price=result.stale_price,
        data_as_of=result.data_as_of,
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
