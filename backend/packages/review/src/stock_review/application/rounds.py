"""回合查询应用层：DTO + 列表 / 详情 / 明细读取（API-029~031 依赖）。

DTO 字段与 DB 列一一对应，金额/数量/比率全部保留标度整数——定点字符串转换只在
API 边界层做（`stock_api/routers/rounds.py`），应用层不做展示格式化。
跨租户/不存在的回合一律抛 `NotFoundError`（只泄露“不存在”，不泄露“属于别人”）。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy.ext.asyncio import AsyncSession
from stock_contracts.errors import NotFoundError

from stock_review.models import Round, RoundFill
from stock_review.repositories.rounds import RoundRepository


@dataclass(slots=True)
class RoundDTO:
    """回合返回体（标度整数）；`id` 即 API 的 `round_id`。"""

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
    fills_count: int
    rebuilt_at: datetime
    created_at: datetime
    updated_at: datetime

    @property
    def round_id(self) -> str:
        """API 命名别名（`RoundOut.round_id`）。"""
        return self.id


@dataclass(slots=True)
class FillDTO:
    """回合配对明细返回体（标度整数）；`id` 即 API 的 `fill_id`。"""

    id: int
    round_id: str
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

    @property
    def fill_id(self) -> int:
        """API 命名别名（`RoundFillOut.fill_id`）。"""
        return self.id


def _round_dto(row: Round) -> RoundDTO:
    return RoundDTO(
        id=row.id,
        security_code=row.security_code,
        security_name=row.security_name,
        status=row.status,
        open_trade_id=row.open_trade_id,
        close_trade_id=row.close_trade_id,
        open_date=row.open_date,
        close_date=row.close_date,
        quantity_units=row.quantity_units,
        cost_units=row.cost_units,
        proceeds_units=row.proceeds_units,
        dividend_units=row.dividend_units,
        pnl_units=row.pnl_units,
        return_rate_units=row.return_rate_units,
        holding_days=row.holding_days,
        data_error=row.data_error,
        oversell_units=row.oversell_units,
        fills_count=row.fills_count,
        rebuilt_at=row.rebuilt_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _fill_dto(row: RoundFill) -> FillDTO:
    return FillDTO(
        id=row.id,
        round_id=row.round_id,
        sequence=row.sequence,
        buy_trade_id=row.buy_trade_id,
        sell_trade_id=row.sell_trade_id,
        quantity_units=row.quantity_units,
        buy_cost_units=row.buy_cost_units,
        sell_proceeds_units=row.sell_proceeds_units,
        dividend_units=row.dividend_units,
        pnl_units=row.pnl_units,
        open_date=row.open_date,
        close_date=row.close_date,
        holding_days=row.holding_days,
    )


async def list_rounds(
    session: AsyncSession,
    user_id: uuid.UUID,
    *,
    status: str | None = None,
    security_code: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    cursor: str | None = None,
    limit: int = 50,
) -> tuple[list[RoundDTO], str | None]:
    """游标分页列回合（`close_date DESC NULLS LAST, id DESC`）。"""
    rows, next_cursor = await RoundRepository(session).page(
        user_id,
        status=status,
        security_code=security_code,
        date_from=date_from,
        date_to=date_to,
        cursor=cursor,
        limit=limit,
    )
    return [_round_dto(row) for row in rows], next_cursor


async def get_round(session: AsyncSession, user_id: uuid.UUID, round_id: str) -> RoundDTO:
    row = await RoundRepository(session).by_id(user_id, round_id)
    if row is None:
        raise NotFoundError(detail=f"回合 {round_id} 不存在")
    return _round_dto(row)


async def list_round_fills(
    session: AsyncSession, user_id: uuid.UUID, round_id: str
) -> list[FillDTO]:
    repo = RoundRepository(session)
    if await repo.by_id(user_id, round_id) is None:
        raise NotFoundError(detail=f"回合 {round_id} 不存在")
    return [_fill_dto(row) for row in await repo.fills(user_id, round_id)]


__all__ = [
    "FillDTO",
    "RoundDTO",
    "get_round",
    "list_round_fills",
    "list_rounds",
]
