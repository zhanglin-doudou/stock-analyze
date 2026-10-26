"""资产表仓储（Period2-16）：分页 / 增量指纹 / 删除区间 / 批量插入 / 状态 CAS。

约定：
- 所有方法显式收 `user_id`（多租户第一道防线；RLS 是第二道）。
- **事务由调用方持有**：本类不 commit / rollback（job handler 的租户事务负责原子性）。
- 稳定序与 `TradeRepository.balances_ordered` 完全一致：
  `trade_date, line_no NULLS FIRST, side=missing 优先, id`（资产表逐日推演必须与其同序，
  否则同日余额取值/期初反推不可复现）。
- 读流水**含软删**：软删/恢复/修正都走 ORM UPDATE（刷新 `updated_at`），指纹能捕获；
  计算只用 `deleted_at IS NULL`。
- 游标复用 `stock_platform.cursor`（载荷 `{"d": "YYYY-MM-DD", "i": id}`），
  非法游标一律 `INVALID_CURSOR`；排序白名单 `trade_date` / `-trade_date`。
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any, cast

from sqlalchemy import case, delete, exists, func, insert, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement
from stock_contracts.constants import CURSOR_MAX_LIMIT
from stock_contracts.enums import Side
from stock_contracts.errors import ApiError, ErrorCode
from stock_ledger.models import Trade
from stock_platform.cursor import decode_cursor, encode_cursor

from stock_review.models import AssetBuildState as AssetBuildStateRow
from stock_review.models import AssetDaily

# 列表排序白名单（默认日期倒序）。
ASSET_SORTS: frozenset[str] = frozenset({"trade_date", "-trade_date"})


def _stable_order() -> tuple[ColumnElement[Any], ...]:
    """流水稳定序（与 `TradeRepository.balances_ordered` 同序）。"""
    return (
        Trade.trade_date.asc(),
        Trade.line_no.asc().nulls_first(),
        case((Trade.side == Side.MISSING.value, 0), else_=1).asc(),
        Trade.id.asc(),
    )


class AssetRepository:
    """资产表仓储；构造只注入 session，所有查询强制带 `user_id`。"""

    __slots__ = ("_session",)

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ── 状态（asset_build_state）──────────────────────────────
    async def get_state(self, user_id: uuid.UUID) -> AssetBuildStateRow | None:
        return await self._session.get(AssetBuildStateRow, user_id)

    async def insert_state(self, **fields: object) -> None:
        """插入一行构建状态（调用方保证该用户尚无 state；version 由调用方给）。"""
        self._session.add(AssetBuildStateRow(**cast("dict[str, Any]", fields)))
        await self._session.flush()

    async def update_state_cas(
        self, user_id: uuid.UUID, *, expected_version: int, **fields: object
    ) -> None:
        """乐观锁更新状态：`WHERE user_id=:uid AND version=:expected`；0 行 → VERSION_CONFLICT。"""
        stmt = (
            update(AssetBuildStateRow)
            .where(
                AssetBuildStateRow.user_id == user_id,
                AssetBuildStateRow.version == expected_version,
            )
            .values(**fields, version=expected_version + 1)
        )
        result = await self._session.execute(stmt)
        if not getattr(result, "rowcount", 0):
            raise ApiError(
                ErrorCode.VERSION_CONFLICT,
                detail="资产构建状态已被并发修改，请重试",
            )

    # ── 流水读取 / 指纹 ──────────────────────────────────────
    async def trade_fingerprint(self, user_id: uuid.UUID) -> tuple[int, int, datetime | None]:
        """变更指纹：`(COUNT(*), SUM(version), MAX(updated_at))`，**含软删**。"""
        row = (
            await self._session.execute(
                select(
                    func.count(),
                    func.coalesce(func.sum(Trade.version), 0),
                    func.max(Trade.updated_at),
                ).where(Trade.user_id == user_id)
            )
        ).one()
        return int(row[0] or 0), int(row[1] or 0), cast("datetime | None", row[2])

    async def load_trades_including_deleted(self, user_id: uuid.UUID) -> list[Trade]:
        """读取该用户全部流水（含软删），稳定序与 `balances_ordered` 一致。"""
        stmt = select(Trade).where(Trade.user_id == user_id).order_by(*_stable_order())
        return list(await self._session.scalars(stmt))

    async def dirty_trades(self, user_id: uuid.UUID, *, since_updated_at: datetime) -> list[Trade]:
        """`updated_at >= since_updated_at` 的流水（含软删），稳定序；供脏窗口定位。"""
        stmt = (
            select(Trade)
            .where(Trade.user_id == user_id, Trade.updated_at >= since_updated_at)
            .order_by(*_stable_order())
        )
        return list(await self._session.scalars(stmt))

    # ── 单行读取 ────────────────────────────────────────────
    async def get_row(self, user_id: uuid.UUID, trade_date: date) -> AssetDaily | None:
        stmt = select(AssetDaily).where(
            AssetDaily.user_id == user_id, AssetDaily.trade_date == trade_date
        )
        return await self._session.scalar(stmt)

    # ── 写：删除 / 插入 / 计数 ────────────────────────────────
    async def delete_all(self, user_id: uuid.UUID) -> int:
        result = await self._session.execute(
            delete(AssetDaily).where(AssetDaily.user_id == user_id)
        )
        return int(getattr(result, "rowcount", 0) or 0)

    async def delete_range(self, user_id: uuid.UUID, date_from: date, date_to: date) -> int:
        """删除 `[date_from, date_to]` 闭区间内的行（增量重写窗口）。"""
        result = await self._session.execute(
            delete(AssetDaily).where(
                AssetDaily.user_id == user_id,
                AssetDaily.trade_date >= date_from,
                AssetDaily.trade_date <= date_to,
            )
        )
        return int(getattr(result, "rowcount", 0) or 0)

    async def delete_after(self, user_id: uuid.UUID, trade_date: date) -> int:
        """删除 `trade_date` 之后的行（清理旧构建残留的“未来”行）。"""
        result = await self._session.execute(
            delete(AssetDaily).where(
                AssetDaily.user_id == user_id, AssetDaily.trade_date > trade_date
            )
        )
        return int(getattr(result, "rowcount", 0) or 0)

    async def insert_rows(self, rows: list[dict[str, object]]) -> None:
        """批量插入资产日行（调用方已删除将被重写的区间）。"""
        if not rows:
            return
        await self._session.execute(insert(AssetDaily), rows)
        await self._session.flush()

    async def count(self, user_id: uuid.UUID) -> int:
        return int(
            await self._session.scalar(
                select(func.count()).select_from(AssetDaily).where(AssetDaily.user_id == user_id)
            )
            or 0
        )

    async def overall_status(self, user_id: uuid.UUID) -> str:
        """整体状态：存在任意 `status != 'ok'` 的行 → degraded，否则 ok。"""
        non_ok = await self._session.scalar(
            select(
                exists().where(
                    AssetDaily.user_id == user_id,
                    AssetDaily.status != "ok",
                )
            )
        )
        return "degraded" if non_ok else "ok"

    # ── 分页 ────────────────────────────────────────────────
    async def page(
        self,
        user_id: uuid.UUID,
        *,
        definition_version: str,
        limit: int,
        cursor: str | None,
        date_from: date | None,
        date_to: date | None,
        status: str | None,
        sort: str,
    ) -> tuple[list[AssetDaily], str | None]:
        """按 `(trade_date, id)` 的游标分页；只返回与 state 版本一致的行。

        `sort` ∈ {"trade_date", "-trade_date"}（默认 -trade_date）；`date_from/date_to`
        为**闭区间**过滤；`status` 为行状态枚举过滤。
        """
        if sort not in ASSET_SORTS:
            raise ApiError(ErrorCode.INVALID_SORT_FIELD, detail=f"不支持的排序字段: {sort}")
        capped = max(1, min(limit, CURSOR_MAX_LIMIT))
        descending = sort == "-trade_date"
        stmt = select(AssetDaily).where(
            AssetDaily.user_id == user_id,
            AssetDaily.definition_version == definition_version,
        )
        if status is not None:
            stmt = stmt.where(AssetDaily.status == status)
        if date_from is not None:
            stmt = stmt.where(AssetDaily.trade_date >= date_from)
        if date_to is not None:
            stmt = stmt.where(AssetDaily.trade_date <= date_to)
        if cursor is not None:
            stmt = stmt.where(*_cursor_condition(cursor, descending=descending))
        if descending:
            stmt = stmt.order_by(AssetDaily.trade_date.desc(), AssetDaily.id.desc())
        else:
            stmt = stmt.order_by(AssetDaily.trade_date.asc(), AssetDaily.id.asc())
        stmt = stmt.limit(capped + 1)
        rows = list(await self._session.scalars(stmt))
        has_more = len(rows) > capped
        rows = rows[:capped]
        next_cursor: str | None = None
        if has_more and rows:
            last = rows[-1]
            next_cursor = encode_cursor({"d": last.trade_date.isoformat(), "i": last.id})
        return rows, next_cursor


def _cursor_condition(cursor: str, *, descending: bool) -> tuple[ColumnElement[bool], ...]:
    """游标 → 排序键之后的 WHERE 条件（排序方向决定比较符）。"""
    payload = decode_cursor(cursor)
    try:
        cursor_date = date.fromisoformat(payload["d"])
        cursor_id = payload["i"]
    except (KeyError, ValueError) as exc:
        raise ApiError(ErrorCode.INVALID_CURSOR, detail="游标字段缺失") from exc
    if descending:
        return (
            or_(
                AssetDaily.trade_date < cursor_date,
                (AssetDaily.trade_date == cursor_date) & (AssetDaily.id < cursor_id),
            ),
        )
    return (
        or_(
            AssetDaily.trade_date > cursor_date,
            (AssetDaily.trade_date == cursor_date) & (AssetDaily.id > cursor_id),
        ),
    )


__all__ = ["ASSET_SORTS", "AssetRepository"]
