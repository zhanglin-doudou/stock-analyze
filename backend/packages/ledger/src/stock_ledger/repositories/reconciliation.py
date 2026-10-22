"""对账结果仓储（按 user_id + 周期 upsert）。

对账是幂等的：同一周期重复核对应覆盖旧结果而非堆积多份，因此以
`(user_id, period_start, period_end)` 唯一约束 + upsert 落库。
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from stock_platform.ids import PREFIX_RECON, new_id

from stock_ledger.models import Reconciliation


class ReconciliationRepository:
    """对账结果读写。"""

    __slots__ = ("_session",)

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def upsert(
        self,
        user_id: uuid.UUID,
        *,
        period_start: date,
        period_end: date,
        status: str,
        break_count: int,
        checked_rows: int,
        epsilon_units: int,
        breaks: list[dict[str, Any]],
        data_as_of: datetime,
        generated_at: datetime,
    ) -> Reconciliation:
        existing = await self.by_period(user_id, period_start, period_end)
        if existing is None:
            row = Reconciliation(
                id=new_id(PREFIX_RECON),
                user_id=user_id,
                period_start=period_start,
                period_end=period_end,
                status=status,
                break_count=break_count,
                checked_rows=checked_rows,
                epsilon_units=epsilon_units,
                breaks=breaks,
                data_as_of=data_as_of,
                generated_at=generated_at,
                version=1,
            )
            self._session.add(row)
        else:
            row = existing
            row.status = status
            row.break_count = break_count
            row.checked_rows = checked_rows
            row.epsilon_units = epsilon_units
            row.breaks = breaks
            row.data_as_of = data_as_of
            row.generated_at = generated_at
            row.version += 1
        await self._session.flush()
        return row

    async def by_period(
        self, user_id: uuid.UUID, period_start: date, period_end: date
    ) -> Reconciliation | None:
        return await self._session.scalar(
            select(Reconciliation).where(
                Reconciliation.user_id == user_id,
                Reconciliation.period_start == period_start,
                Reconciliation.period_end == period_end,
            )
        )

    async def latest(self, user_id: uuid.UUID) -> Reconciliation | None:
        return await self._session.scalar(
            select(Reconciliation)
            .where(Reconciliation.user_id == user_id)
            .order_by(Reconciliation.generated_at.desc())
            .limit(1)
        )


__all__ = ["ReconciliationRepository"]
