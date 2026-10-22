"""隔离区仓储：脏行的旁存、查询与回填。

隔离区存在的意义是“既不污染统计，也不丢用户数据”。每条记录保留原始行 + 原因码 +
建议规则，用户修正后可用 `resolve` 回指生成的明细行。
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from stock_contracts.errors import NotFoundError

from stock_ledger.models import QuarantineRow


class QuarantineRepository:
    """隔离区行仓储。"""

    __slots__ = ("_session",)

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add_many(
        self, user_id: uuid.UUID, rows: Sequence[dict[str, Any]]
    ) -> list[QuarantineRow]:
        """批量落隔离行；每个元素需含 line_no/raw/reason_code/suggested_rule_id/batch_id。"""
        models = [
            QuarantineRow(
                user_id=user_id,
                batch_id=row.get("batch_id"),
                line_no=row.get("line_no"),
                raw=row.get("raw", {}),
                reason_code=row["reason_code"],
                suggested_rule_id=row.get("suggested_rule_id"),
            )
            for row in rows
        ]
        if models:
            self._session.add_all(models)
            await self._session.flush()
        return models

    async def add(
        self,
        user_id: uuid.UUID,
        *,
        reason_code: str,
        raw: dict[str, Any],
        batch_id: str | None = None,
        line_no: int | None = None,
        suggested_rule_id: str | None = None,
    ) -> QuarantineRow:
        row = QuarantineRow(
            user_id=user_id,
            batch_id=batch_id,
            line_no=line_no,
            raw=raw,
            reason_code=reason_code,
            suggested_rule_id=suggested_rule_id,
        )
        self._session.add(row)
        await self._session.flush()
        return row

    async def by_id(self, user_id: uuid.UUID, quarantine_id: int) -> QuarantineRow | None:
        return await self._session.scalar(
            select(QuarantineRow).where(
                QuarantineRow.user_id == user_id, QuarantineRow.id == quarantine_id
            )
        )

    async def list_for_batch(self, user_id: uuid.UUID, batch_id: str) -> list[QuarantineRow]:
        rows = await self._session.scalars(
            select(QuarantineRow)
            .where(QuarantineRow.user_id == user_id, QuarantineRow.batch_id == batch_id)
            .order_by(QuarantineRow.line_no.asc().nulls_last(), QuarantineRow.id.asc())
        )
        return list(rows)

    async def page(
        self, user_id: uuid.UUID, *, resolved: bool | None = None, limit: int = 50
    ) -> list[QuarantineRow]:
        stmt = select(QuarantineRow).where(QuarantineRow.user_id == user_id)
        if resolved is not None:
            stmt = stmt.where(QuarantineRow.resolved.is_(resolved))
        stmt = stmt.order_by(QuarantineRow.id.desc()).limit(max(1, min(limit, 200)))
        return list(await self._session.scalars(stmt))

    async def count_unresolved(self, user_id: uuid.UUID) -> int:
        return int(
            await self._session.scalar(
                select(func.count())
                .select_from(QuarantineRow)
                .where(QuarantineRow.user_id == user_id, QuarantineRow.resolved.is_(False))
            )
            or 0
        )

    async def resolve(
        self,
        user_id: uuid.UUID,
        quarantine_id: int,
        *,
        resolved_trade_id: str | None,
    ) -> QuarantineRow:
        row = await self.by_id(user_id, quarantine_id)
        if row is None:
            raise NotFoundError(detail=f"隔离行 {quarantine_id} 不存在")
        row.resolved = True
        row.resolved_trade_id = resolved_trade_id
        await self._session.flush()
        return row


__all__ = ["QuarantineRepository"]
