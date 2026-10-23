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
from stock_core.values import Money, Price, Qty, Ratio
from stock_platform.tenant import TenantContext

from stock_api.deps import get_market_data, get_state, get_tenant
from stock_api.routing import StrictQueryRoute
from stock_api.schemas import NavHoldingOut, NavOut, NavPointOut, NavSummaryOut

router = APIRouter(tags=["nav"], route_class=StrictQueryRoute)

_PERIOD_RE = re.compile(r"^(\d{4})-(\d{2})$")
_DEFINITION_VERSION = "1.0"

# cost (scale=2) / qty (scale=4) → price (scale=6): cost * 1e8 / qty
_COST_QTY_TO_PRICE_SCALE = 10**8
# 比率标度（scale=8）：金额比值 → 收益率标度整数
_RATIO_SCALE = 10**8


def _to_3dp(units: int, scale: int) -> str:
    """标度整数 → 三位小数字符串（截断而非四舍五入，保持零口径可预测）。"""
    sign = "-" if units < 0 else ""
    magnitude = abs(units)
    if scale == 0:
        return f"{sign}{magnitude}.000"
    int_part, frac_units = divmod(magnitude, 10**scale)
    frac = f"{frac_units:0{scale}d}"
    # 截断到三位：如果 scale > 3，取前 3 位；如果 scale < 3，补 0
    frac = frac[:3] if scale >= 3 else frac + "0" * (3 - scale)
    return f"{sign}{int_part}.{frac}"


def _to_3dp_trim(units: int, scale: int) -> str:
    """三位小数后去掉尾零（`9.100` → `9.1`），用于持仓明细展示。"""
    text = _to_3dp(units, scale)
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return "0" if text == "-0" else text


def _pct_2dp(ratio_units: int) -> str:
    """比率标度整数 → 两位小数百分比字符串（避免出现 "-0.00%"）。"""
    text = Ratio(ratio_units).as_pct(2)
    return "0.00%" if text == "-0.00%" else text


def _nav_holding_out(
    code: str, name: str, qty_units: int, cost_units: int, close_units: int, market_value_units: int
) -> NavHoldingOut:
    """持仓快照 → NavHoldingOut（三位小数字符串）。"""
    if qty_units > 0 and cost_units > 0:
        avg_cost_units = cost_units * _COST_QTY_TO_PRICE_SCALE // qty_units
    else:
        avg_cost_units = 0
    return NavHoldingOut(
        security_code=code,
        security_name=name,
        quantity=_to_3dp_trim(qty_units, Qty.scale),
        avg_cost=_to_3dp_trim(avg_cost_units, Price.scale),
        close_price=_to_3dp_trim(close_units, Price.scale),
        market_value=_to_3dp_trim(market_value_units, Money.scale),
    )


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

    holdings_map = result.holdings_by_date
    prev_total_units: int | None = None

    for index, row in enumerate(rows):
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

        holdings_list: list[NavHoldingOut] = []
        total_assets_3dp = _to_3dp(0, Money.scale)
        prev_total_assets: str | None = None
        daily_pnl: str | None = None
        pct = _pct_2dp(row.return_units)
        if not is_blocked:
            total_assets_3dp = _to_3dp(row.total_value_units, Money.scale)
            # 首日没有上月点：用「上一交易日收盘资产」补昨日资产
            prev_units = prev_total_units
            if prev_units is None and index == 0:
                prev_units = result.prev_day_value_units
            if prev_units is not None:
                prev_total_assets = _to_3dp(prev_units, Money.scale)
                pnl_units = row.total_value_units - row.cashflow_units - prev_units
                # 日收益额 = 当日总资产 − 当日外部现金流 − 昨日资产
                daily_pnl = _to_3dp(pnl_units, Money.scale)
                if index == 0 and prev_units > 0:
                    # TWR 首点为基准（return 恒为 0），展示口径按当日盈亏/昨日资产计算
                    pct = _pct_2dp(pnl_units * _RATIO_SCALE // prev_units)
            prev_total_units = row.total_value_units
            for snap in holdings_map.get(row.trade_date, []):
                holdings_list.append(
                    _nav_holding_out(
                        snap.security_code,
                        snap.security_name,
                        snap.quantity_units,
                        snap.cost_units,
                        snap.close_units,
                        snap.market_value_units,
                    )
                )
        else:
            # 阻断日切断「昨日资产」链条：下一可输出日的昨日资产不可得。
            prev_total_units = None

        points.append(
            NavPointOut(
                date=row.trade_date,
                unit_nav=str(Price(row.nav_units)),
                twr_daily=str(Ratio(row.return_units)),
                twr_daily_pct=pct,
                cashflow=str(Money(row.cashflow_units)),
                blocked=is_blocked,
                blocked_reason=row.blocked_reason if is_blocked else None,
                total_assets=total_assets_3dp,
                prev_total_assets=prev_total_assets,
                daily_pnl=daily_pnl,
                holdings=holdings_list,
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
        assumption=cast(Literal["statement", "back_solved"], result.assumption)
        if result.assumption
        else None,
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
