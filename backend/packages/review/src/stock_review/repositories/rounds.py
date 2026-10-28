"""回合仓储：游标分页、按证券删除、批量插入、计数。

约定：
- 所有方法显式收 `user_id`（多租户第一道防线；RLS 是第二道）。
- 游标复用 `stock_platform.cursor`（载荷 `{"c": close_date|"", "i": round_id}`），
  非法游标一律 `INVALID_CURSOR`，不暴露内部排序键。
- 排序与索引一致：`close_date DESC NULLS LAST, id DESC`（open 回合在最后），
  新写入不会导致翻页错位。
- `RoundInput/FillInput` 是应用层输入 dataclass（非 ORM），由 rebuild 构造；
  `RoundInput.id` 必须是确定性 ID（见 `application/rebuild.build_round_id`）。
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime

from sqlalchemy import delete, func, insert, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement
from stock_contracts.constants import CURSOR_MAX_LIMIT
from stock_contracts.errors import ApiError, ErrorCode
from stock_platform.cursor import decode_cursor, encode_cursor

from stock_review.models import Round, RoundFill


@dataclass(slots=True)
class FillInput:
    """一条回合配对明细的输入（标度整数）。"""

    sequence: int
    buy_trade_id: str
    sell_trade_id: str
    quantity_units: int
    buy_cost_units: int
    sell_proceeds_units: int
    dividend_units: int
    pnl_units: int
    open_date: date
    close_date: date
    holding_days: int


@dataclass(slots=True)
class RoundInput:
    """一个回合（含明细）的输入；`id` 必须由应用层确定性生成。"""

    id: str
    security_code: str
    security_name: str
    status: str
    open_trade_id: str
    close_trade_id: str | None
    open_date: date
    close_date: date | None
    quantity_units: int
    cost_units: int
    proceeds_units: int
    dividend_units: int
    pnl_units: int
    return_rate_units: int
    holding_days: int
    data_error: bool
    oversell_units: int
    rebuilt_at: datetime
    fills: tuple[FillInput, ...] = field(default_factory=tuple)


def _round_values(user_id: uuid.UUID, item: RoundInput) -> dict[str, object]:
    return {
        "id": item.id,
        "user_id": user_id,
        "security_code": item.security_code,
        "security_name": item.security_name,
        "status": item.status,
        "open_trade_id": item.open_trade_id,
        "close_trade_id": item.close_trade_id,
        "open_date": item.open_date,
        "close_date": item.close_date,
        "quantity_units": item.quantity_units,
        "cost_units": item.cost_units,
        "proceeds_units": item.proceeds_units,
        "dividend_units": item.dividend_units,
        "pnl_units": item.pnl_units,
        "return_rate_units": item.return_rate_units,
        "holding_days": item.holding_days,
        "data_error": item.data_error,
        "oversell_units": item.oversell_units,
        "fills_count": len(item.fills),
        "rebuilt_at": item.rebuilt_at,
    }


def _fill_values(user_id: uuid.UUID, round_id: str, item: FillInput) -> dict[str, object]:
    return {
        "user_id": user_id,
        "round_id": round_id,
        "sequence": item.sequence,
        "buy_trade_id": item.buy_trade_id,
        "sell_trade_id": item.sell_trade_id,
        "quantity_units": item.quantity_units,
        "buy_cost_units": item.buy_cost_units,
        "sell_proceeds_units": item.sell_proceeds_units,
        "dividend_units": item.dividend_units,
        "pnl_units": item.pnl_units,
        "open_date": item.open_date,
        "close_date": item.close_date,
        "holding_days": item.holding_days,
    }


class RoundRepository:
    """回合仓储；构造只注入 session，所有查询强制带 `user_id`。"""

    __slots__ = ("_session",)

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ── 读 ──────────────────────────────────────────────────
    async def by_id(self, user_id: uuid.UUID, round_id: str) -> Round | None:
        return await self._session.scalar(
            select(Round).where(Round.user_id == user_id, Round.id == round_id)
        )

    async def fills(self, user_id: uuid.UUID, round_id: str) -> list[RoundFill]:
        rows = await self._session.scalars(
            select(RoundFill)
            .where(RoundFill.user_id == user_id, RoundFill.round_id == round_id)
            .order_by(RoundFill.sequence)
        )
        return list(rows)

    async def page(
        self,
        user_id: uuid.UUID,
        *,
        status: str | None = None,
        security_code: str | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
        cursor: str | None = None,
        limit: int = 50,
    ) -> tuple[list[Round], str | None]:
        """`close_date DESC NULLS LAST, id DESC` 的游标分页。

        日期过滤作用于 `COALESCE(close_date, open_date)`：open 回合没有平仓日，
        但用户按“日期区间”筛回合时仍应看到区间内开仓的持仓。
        """
        capped = max(1, min(limit, CURSOR_MAX_LIMIT))
        stmt = select(Round).where(Round.user_id == user_id)
        if status is not None:
            stmt = stmt.where(Round.status == status)
        if security_code is not None:
            stmt = stmt.where(Round.security_code == security_code)
        effective_date = func.coalesce(Round.close_date, Round.open_date)
        if date_from is not None:
            stmt = stmt.where(effective_date >= date_from)
        if date_to is not None:
            stmt = stmt.where(effective_date <= date_to)
        if cursor is not None:
            stmt = stmt.where(*_cursor_condition(cursor))
        stmt = stmt.order_by(Round.close_date.desc().nulls_last(), Round.id.desc()).limit(
            capped + 1
        )
        rows = list(await self._session.scalars(stmt))
        has_more = len(rows) > capped
        rows = rows[:capped]
        next_cursor: str | None = None
        if has_more and rows:
            last = rows[-1]
            next_cursor = encode_cursor(
                {
                    "c": last.close_date.isoformat() if last.close_date is not None else "",
                    "i": last.id,
                }
            )
        return rows, next_cursor

    async def count(self, user_id: uuid.UUID) -> int:
        return int(
            await self._session.scalar(
                select(func.count()).select_from(Round).where(Round.user_id == user_id)
            )
            or 0
        )

    async def in_period(self, user_id: uuid.UUID, start: date, end: date) -> list[Round]:
        """指标视图：闭区间 `[start, end]` 内的回合（close_date 优先，open 回合按 open_date）。"""
        effective = func.coalesce(Round.close_date, Round.open_date)
        rows = await self._session.scalars(
            select(Round)
            .where(Round.user_id == user_id, effective >= start, effective <= end)
            .order_by(effective, Round.id)
        )
        return list(rows)

    async def closed_rounds(self, user_id: uuid.UUID) -> list[Round]:
        """全历史已平仓且非 data_error 的回合（Dashboard 计数/最赚最亏）。"""
        rows = await self._session.scalars(
            select(Round)
            .where(
                Round.user_id == user_id,
                Round.status == "closed",
                Round.data_error.is_(False),
            )
            .order_by(Round.close_date.asc().nulls_last(), Round.id.asc())
        )
        return list(rows)

    async def fills_in_period(self, user_id: uuid.UUID, start: date, end: date) -> list[RoundFill]:
        """指标视图：平仓日落在区间内的逐 chunk 明细。"""
        rows = await self._session.scalars(
            select(RoundFill)
            .where(
                RoundFill.user_id == user_id,
                RoundFill.close_date >= start,
                RoundFill.close_date <= end,
            )
            .order_by(RoundFill.close_date, RoundFill.round_id, RoundFill.sequence)
        )
        return list(rows)

    # ── 写 ──────────────────────────────────────────────────
    async def delete_all(self, user_id: uuid.UUID) -> int:
        """删除该用户全部回合（明细由 FK ON DELETE CASCADE 级联）。"""
        result = await self._session.execute(delete(Round).where(Round.user_id == user_id))
        return int(getattr(result, "rowcount", 0) or 0)

    async def delete_for_securities(self, user_id: uuid.UUID, codes: Sequence[str]) -> int:
        """只删指定证券的回合（增量重建：其他证券不受影响）。"""
        unique_codes = sorted({code for code in codes if code})
        if not unique_codes:
            return 0
        result = await self._session.execute(
            delete(Round).where(Round.user_id == user_id, Round.security_code.in_(unique_codes))
        )
        return int(getattr(result, "rowcount", 0) or 0)

    async def insert_batch(self, user_id: uuid.UUID, rounds: Sequence[RoundInput]) -> int:
        """批量插入回合与明细；返回插入的回合数（调用方需先删旧回合）。"""
        if not rounds:
            return 0
        round_rows = [_round_values(user_id, item) for item in rounds]
        await self._session.execute(insert(Round), round_rows)
        fill_rows = [_fill_values(user_id, item.id, fill) for item in rounds for fill in item.fills]
        if fill_rows:
            await self._session.execute(insert(RoundFill), fill_rows)
        await self._session.flush()
        return len(rounds)


def _cursor_condition(cursor: str) -> tuple[ColumnElement[bool], ...]:
    """游标 → 排序键之后的 WHERE 条件（`close_date DESC NULLS LAST, id DESC`）。"""
    payload = decode_cursor(cursor)
    try:
        raw_close = payload.get("c", "")
        cursor_id = payload["i"]
        cursor_date = date.fromisoformat(raw_close) if raw_close else None
    except (KeyError, ValueError) as exc:
        raise ApiError(ErrorCode.INVALID_CURSOR, detail="游标字段缺失") from exc
    if cursor_date is None:
        # 游标停在 open 回合（close_date IS NULL）上：后续只有 null 组里 id 更小的行。
        return (Round.close_date.is_(None), Round.id < cursor_id)
    return (
        or_(
            Round.close_date < cursor_date,
            Round.close_date.is_(None),
            (Round.close_date == cursor_date) & (Round.id < cursor_id),
        ),
    )


__all__ = ["FillInput", "RoundInput", "RoundRepository"]
