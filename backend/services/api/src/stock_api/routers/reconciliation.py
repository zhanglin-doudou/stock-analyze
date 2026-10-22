"""对账端点（API-026）：余额链核对与断链精确定位。

金额一律 **定点字符串**（ledger 返回标度整数，这里转字符串）；禁止 float。
用户维度强制：对账只在本账号的流水上进行。
"""

from __future__ import annotations

import calendar
import re
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from stock_contracts.errors import ApiError, ErrorCode
from stock_core.values import Money
from stock_ledger.application.reconcile import reconcile
from stock_platform.tenant import TenantContext

from stock_api.deps import get_state, get_tenant
from stock_api.routing import StrictQueryRoute
from stock_api.schemas import ReconciliationBreak, ReconciliationOut

router = APIRouter(tags=["reconciliation"], route_class=StrictQueryRoute)

_PERIOD_RE = re.compile(r"^(\d{4})-(\d{2})$")


def _parse_period(period: str) -> tuple[date, date]:
    """YYYY-MM → [当月首日, 当月末日]；非法格式/月份 → 422 INVALID_PERIOD。"""
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
    """period 或 from/to 二者其一；缺省/矛盾 → 422 INVALID_PERIOD。"""
    if period is not None:
        start, end = _parse_period(period)
        return start, end, period
    if date_from is not None and date_to is not None:
        if date_from > date_to:
            raise ApiError(ErrorCode.INVALID_PERIOD, detail="from 必须不晚于 to")
        return date_from, date_to, f"{date_from.isoformat()}~{date_to.isoformat()}"
    raise ApiError(ErrorCode.INVALID_PERIOD, detail="需提供 period 或 from/to")


@router.get("/reconciliation", response_model=ReconciliationOut, operation_id="getReconciliation")
async def get_reconciliation(
    request: Request,
    ctx: Annotated[TenantContext, Depends(get_tenant)],
    period: Annotated[str | None, Query(description="YYYY-MM")] = None,
    date_from: Annotated[date | None, Query(alias="from")] = None,
    date_to: Annotated[date | None, Query(alias="to")] = None,
    include_ok_rows: Annotated[bool, Query()] = False,
) -> ReconciliationOut:
    """API-026：核对资金余额链，断链定位到具体行。

    `include_ok_rows` 由契约保留；ledger 目前只回传断链行，故该参数暂不改变输出。
    """
    user_id = ctx.require_user()
    # 对账结果属于业务数据：admin 只做运维，不可读写明细（Architecture §0 角色模型）。
    ctx.forbid_admin_trades()
    state = get_state(request)
    start, end, label = _resolve_range(period, date_from, date_to)

    async with state.db.session(user_id=user_id, begin=True) as session:
        dto = await reconcile(
            session, clock=state.clock, user_id=user_id, period_start=start, period_end=end
        )

    breaks = [
        ReconciliationBreak(
            trade_id=str(item["trade_id"]),
            line_no=item.get("line_no"),
            trade_date=(date.fromisoformat(item["trade_date"]) if item.get("trade_date") else None),
            expected=str(Money(int(item["expected_units"]))),
            actual=str(Money(int(item["actual_units"]))),
            diff=str(Money(int(item["delta_units"]))),
            filled=bool(item.get("filled", False)),
            filled_trade_id=(str(item["filled_trade_id"]) if item.get("filled_trade_id") else None),
        )
        for item in dto.breaks
    ]
    return ReconciliationOut(
        period=label,
        status=dto.status,
        break_count=dto.break_count,
        breaks=breaks,
        checked_rows=dto.checked_rows,
        filled_count=dto.filled_count,
        epsilon=str(Money(dto.epsilon_units)),
        data_as_of=dto.data_as_of,
        generated_at=dto.generated_at,
    )
