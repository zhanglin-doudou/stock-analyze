"""净值与收益端点（API-032）：份额法 TWR + XIRR/MWR。

金额/净值/收益率一律 **定点字符串**；禁止 float。
本期不含基准对比（beta/alpha/ir 留到 Period2-05），相关字段返回 null。
"""

from __future__ import annotations

import calendar
import re
from datetime import date
from typing import Annotated, Literal, cast

from fastapi import APIRouter, Depends, Query, Request
from stock_contracts.errors import ApiError, ErrorCode
from stock_core.values import Money, Price, Ratio
from stock_platform.tenant import TenantContext

from stock_api.deps import get_market_data, get_state, get_tenant
from stock_api.routing import StrictQueryRoute
from stock_api.schemas import NavOut, NavPointOut, NavSummaryOut

router = APIRouter(tags=["nav"], route_class=StrictQueryRoute)

_PERIOD_RE = re.compile(r"^(\d{4})-(\d{2})$")
_DEFINITION_VERSION = "1.0"


def _parse_period(period: str) -> tuple[date, date]:
    matched = _PERIOD_RE.match(period)
    if matched is None:
        raise ApiError(ErrorCode.INVALID_PERIOD, detail=f"period 必须是 YYYY-MM: {period}")
    year, month = int(matched.group(1)), int(matched.group(2))
    if not 1 <= month <= 12:
        raise ApiError(ErrorCode.INVALID_PERIOD, detail=f"period 月份非法: {period}")
    return date(year, month, 1), date(year, month, calendar.monthrange(year, month)[1])


def _resolve_range(
    period: str | None, date_from: date | None, date_to: date | None
) -> tuple[date, date, str]:
    if period is not None:
        start, end = _parse_period(period)
        return start, end, period
    if date_from is not None and date_to is not None:
        if date_from > date_to:
            raise ApiError(ErrorCode.INVALID_PERIOD, detail="from 必须不晚于 to")
        return date_from, date_to, f"{date_from.isoformat()}~{date_to.isoformat()}"
    raise ApiError(ErrorCode.INVALID_PERIOD, detail="需提供 period 或 from/to")


@router.get("/nav", response_model=NavOut, operation_id="getNav")
async def get_nav(
    request: Request,
    ctx: Annotated[TenantContext, Depends(get_tenant)],
    period: Annotated[str | None, Query(description="YYYY-MM")] = None,
    date_from: Annotated[date | None, Query(alias="from")] = None,
    date_to: Annotated[date | None, Query(alias="to")] = None,
    benchmark: Annotated[str, Query(description="基准代码（本期暂不计算对比）")] = "hs300_tr",
    granularity: Annotated[str, Query(description="daily|weekly（本期仅 daily）")] = "daily",
) -> NavOut:
    """API-032：区间单位净值序列 + TWR/MWR 汇总。

    计算并落库 `review.nav_daily`，blocked 日不返回资产值（unit_nav/twr_daily 为 "0"）。
    """
    if granularity != "daily":
        raise ApiError(ErrorCode.VALIDATION_ERROR, detail="granularity 仅支持 daily")

    user_id = ctx.require_user()
    ctx.forbid_admin_trades()
    state = get_state(request)
    market_data = get_market_data(request)
    start, end, label = _resolve_range(period, date_from, date_to)

    from stock_review.application.nav import (
        compute_and_store_nav,
        fetch_nav_for_period,
    )

    async with state.db.session(user_id=user_id, begin=True) as session:
        result = await compute_and_store_nav(
            session,
            user_id=user_id,
            period_start=start,
            period_end=end,
            market_data=market_data,
            clock=state.clock,
        )
        rows = await fetch_nav_for_period(
            session, user_id=user_id, period_start=start, period_end=end
        )
        await session.commit()

    # 构造响应
    points: list[NavPointOut] = []
    blocked_ranges: list[dict[str, str]] = []
    overall_status: str = "ok"
    blocked_start: date | None = None

    for row in rows:
        is_blocked = row.status == "blocked"
        if is_blocked:
            overall_status = "blocked"
            if blocked_start is None:
                blocked_start = row.trade_date
        else:
            if blocked_start is not None:
                blocked_ranges.append(
                    {"from": blocked_start.isoformat(), "to": row.trade_date.isoformat()}
                )
                blocked_start = None
            if row.status == "degraded" and overall_status != "blocked":
                overall_status = "degraded"

        points.append(
            NavPointOut(
                date=row.trade_date,
                unit_nav=str(Price(row.nav_units)),
                twr_daily=str(Ratio(row.return_units)),
                cashflow=str(Money(row.cashflow_units)),
                blocked=is_blocked,
                blocked_reason=row.blocked_reason if is_blocked else None,
            )
        )
    if blocked_start is not None and rows:
        blocked_ranges.append(
            {"from": blocked_start.isoformat(), "to": rows[-1].trade_date.isoformat()}
        )

    if result.blocked_reasons:
        overall_status = "blocked"

    # MWR 与 TWR 都是年化口径（XIRR 用 ACT/365），差额才能对齐比较（PRD 附录 A6）
    annualized_twr_units = result.series.annualized_twr_units if result.series else 0
    mwr_minus_twr = result.mwr_units - annualized_twr_units
    summary = NavSummaryOut(
        mwr=str(Ratio(result.mwr_units)),
        twr=str(Ratio(result.series.twr_units if result.series else 0)),
        annualized_twr=str(Ratio(annualized_twr_units)),
        mwr_minus_twr=str(Ratio(mwr_minus_twr)),
        n_days=result.series.natural_days if result.series else 0,
    )

    return NavOut(
        period=label,
        points=points,
        summary=summary,
        status=cast(Literal["ok", "blocked", "degraded"], overall_status),
        blocked_ranges=blocked_ranges,
        benchmark_id=benchmark,
        data_as_of=state.clock.now(),
        definition_version=_DEFINITION_VERSION,
    )
