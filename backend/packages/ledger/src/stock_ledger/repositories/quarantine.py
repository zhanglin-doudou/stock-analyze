"""隔离区仓储：脏行的旁存、查询与回填。

隔离区存在的意义是“既不污染统计，也不丢用户数据”。每条记录保留原始行 + 原因码 +
建议规则，用户修正后可用 `resolve` 回指生成的明细行。
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import date
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from stock_contracts.enums import QuarantineReason
from stock_contracts.errors import NotFoundError
from stock_contracts.import_fields import FIELD_ALIASES

from stock_ledger.models import QuarantineRow


# 隔离行没有日期列：日期在 `raw` 的日期别名里，格式随账单模板不同。
def _parse_raw_date(text: Any) -> date | None:
    """解析隔离行里的日期文本（`2021-07-12` / `2021/7/12` / `20210712`）。"""
    cleaned = str(text).strip().strip('="')[:10]
    if len(cleaned) == 8 and cleaned.isdigit():
        cleaned = f"{cleaned[:4]}-{cleaned[4:6]}-{cleaned[6:]}"
    parts = cleaned.replace("/", "-").split("-")
    if len(parts) != 3 or not all(part.isdigit() for part in parts):
        return None
    try:
        return date(int(parts[0]), int(parts[1]), int(parts[2]))
    except ValueError:
        return None


def _raw_row_date(raw: dict[str, Any]) -> date | None:
    """从隔离行原始 JSON 里取交易日（兼容新映射「发生日期」与旧格式「日期」）。"""
    for alias in FIELD_ALIASES["trade_date"]:
        text = raw.get(alias)
        if not text:
            continue
        parsed = _parse_raw_date(text)
        if parsed is not None:
            return parsed
    return None


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

    async def earliest_unresolved_break_date(
        self,
        user_id: uuid.UUID,
        *,
        after: date | None = None,
        on_or_before: date | None = None,
    ) -> date | None:
        """日期窗口内最早一条未处理的余额链断开日期（`RECONCILIATION_BREAK`）。

        用于在不对账页面上直接给出「哪一天开始断」：隔离行本身不带日期列，日期从
        `raw` 的日期别名解析（解析失败的行跳过，不影响其余行）。窗口用于把断点定位到
        某次现金缺口附近，避免被历史上已自愈的断链（净额为 0）抢走。
        """
        rows = await self._session.scalars(
            select(QuarantineRow.raw).where(
                QuarantineRow.user_id == user_id,
                QuarantineRow.reason_code == QuarantineReason.RECONCILIATION_BREAK.value,
                QuarantineRow.resolved.is_(False),
            )
        )
        dates = [item for raw in rows if (item := _raw_row_date(raw)) is not None]
        if after is not None:
            dates = [item for item in dates if item >= after]
        if on_or_before is not None:
            dates = [item for item in dates if item <= on_or_before]
        return min(dates) if dates else None

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
