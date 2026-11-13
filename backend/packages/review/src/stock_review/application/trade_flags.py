"""worker：按用户生效阈值重建账单旗标（API-097）。

参数 `{"scope": "all" | "months", "months": ["YYYY-MM", ...]}`：
- `months`：只重建列出的自然月（API 层已去重/排序且 ≤120）；
- `all`：从首笔交易所在月到最近一笔交易所在月；超过 120 个月只重建最近 120 个月
  并在 result 标 `truncated=true`。

单 job 单事务（runner `tenant_tx`）：逐月「同月先删后插」，重跑幂等；每月前检查
取消标记，progress 单调不减。阈值在任务开始时读取一次，整个 job 用同一套口径。
"""

from __future__ import annotations

import logging
import uuid
from datetime import date
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from stock_contracts.clock import Clock
from stock_contracts.enums import JobStatus
from stock_contracts.errors import ApiError, ErrorCode
from stock_ledger.models import Trade
from stock_platform.jobs import JobQueue
from stock_platform.ports import MarketDataSource

from stock_review.metrics.context import parse_period_label
from stock_review.trade_flags import build_trade_flags_for_user, load_flag_config

logger = logging.getLogger(__name__)

__all__ = ["MAX_MONTHS", "months_between", "rebuild_trade_flags_job"]

#: 单次任务覆盖的自然月上限（与 API-097 请求上限一致）。
MAX_MONTHS = 120


def _month_label(day: date) -> str:
    return f"{day.year:04d}-{day.month:02d}"


def months_between(start: str, end: str) -> list[str]:
    """左闭右闭的月份序列（`YYYY-MM` 字符串）。"""
    start_year, start_month = (int(part) for part in start.split("-"))
    end_year, end_month = (int(part) for part in end.split("-"))
    months: list[str] = []
    year, month = start_year, start_month
    while (year, month) <= (end_year, end_month):
        months.append(f"{year:04d}-{month:02d}")
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return months


async def _trade_month_bounds(session: AsyncSession, user_id: uuid.UUID) -> tuple[str, str] | None:
    where = (Trade.user_id == user_id, Trade.deleted_at.is_(None))
    first = await session.scalar(select(func.min(Trade.trade_date)).where(*where))
    if first is None:
        return None
    last = await session.scalar(select(func.max(Trade.trade_date)).where(*where))
    if last is None:
        return None
    return _month_label(first), _month_label(last)


async def rebuild_trade_flags_job(
    queue: JobQueue,
    session: AsyncSession,
    job: dict[str, Any],
    clock: Clock,
    market_data: MarketDataSource,
) -> dict[str, Any]:
    """按 scope 逐月重建；空范围直接 finish（无交易不是错误）。"""
    user_id = uuid.UUID(str(job["user_id"]))
    job_id = str(job["id"])
    params: dict[str, Any] = dict(job.get("params") or {})
    scope = str(params.get("scope") or "all")
    months = [str(item) for item in params.get("months") or []]
    truncated = False

    if scope == "months":
        if not months:
            raise ApiError(
                ErrorCode.VALIDATION_ERROR,
                detail="scope=months 时必须给出 months",
            )
    else:
        bounds = await _trade_month_bounds(session, user_id)
        if bounds is None:
            # 无交易不是错误：显式成功 + 空范围。
            empty: dict[str, Any] = {
                "months": [],
                "months_count": 0,
                "trades": 0,
                "rows": 0,
                "truncated": False,
            }
            await queue.finish(session, job_id, status=JobStatus.SUCCEEDED, result=empty)
            return {"job_id": job_id, **empty}
        months = months_between(*bounds)
        if len(months) > MAX_MONTHS:
            months = months[-MAX_MONTHS:]
            truncated = True

    config, config_hash = await load_flag_config(session, user_id)
    total_trades = 0
    total_rows = 0
    await queue.progress(session, job_id, percent=1, stage="trade_flags", message=None)
    for index, month in enumerate(months, start=1):
        if await queue.is_cancel_requested(session, job_id):
            await queue.finish(
                session,
                job_id,
                status=JobStatus.CANCELLED,
                result={"cancelled": True, "months_done": index - 1},
            )
            return {"job_id": job_id, "cancelled": True, "months_done": index - 1}
        built = await build_trade_flags_for_user(
            session,
            user_id,
            period=parse_period_label(month, today=clock.today()),
            market_data=market_data,
            clock=clock,
            config=config,
        )
        total_trades += built.trades
        total_rows += built.rows
        percent = min(95, 5 + index * 90 // len(months))
        await queue.progress(session, job_id, percent=percent, stage="trade_flags", message=month)

    logger.info(
        "trade_flags.rebuild_done",
        extra={
            "user_id": str(user_id),
            "months": len(months),
            "trades": total_trades,
            "rows": total_rows,
            "flags_hash": config_hash,
        },
    )
    result: dict[str, Any] = {
        "months": months,
        "months_count": len(months),
        "trades": total_trades,
        "rows": total_rows,
        "flags_hash": config_hash,
        "truncated": truncated,
    }
    await queue.finish(session, job_id, status=JobStatus.SUCCEEDED, result=result)
    return {"job_id": job_id, **result}
