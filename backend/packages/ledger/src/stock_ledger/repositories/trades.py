"""交易明细仓储：游标分页、乐观锁更新、软删/恢复、对账余额链、导出。

游标用 `base64url(json({"d": trade_date, "i": id}))`：把“上一页最后一行”的排序键编码
进 token，比 offset 分页更稳（新写入不会导致翻页错位）。非法游标一律抛 INVALID_CURSOR。
"""

from __future__ import annotations

import base64
import binascii
import json
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import case, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from stock_contracts.constants import CURSOR_MAX_LIMIT
from stock_contracts.enums import Side
from stock_contracts.errors import ApiError, ErrorCode, NotFoundError
from stock_platform.ids import trade_id as new_trade_id

from stock_ledger.models import Trade, TradeHistory

# 允许通过 update_fields 修改的列（其余为系统维护，禁止篡改）
_MUTABLE_FIELDS: frozenset[str] = frozenset(
    {
        "trade_date",
        "security_code",
        "security_name",
        "summary",
        "summary_code",
        "side",
        "quantity_units",
        "price_units",
        "amount_units",
        "commission_units",
        "stamp_tax_units",
        "other_fee_units",
        "cash_balance_units",
        "cash_balance_available",
        "category",
        "account_masked",
        "note",
    }
)


def encode_cursor(payload: dict[str, str]) -> str:
    """排序键 → 不透明游标（URL 安全、无填充）。"""
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_cursor(cursor: str) -> dict[str, str]:
    """不透明游标 → 排序键；任何损坏都按 INVALID_CURSOR 处理。"""
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        raw = base64.urlsafe_b64decode(padded.encode("ascii"))
        data = json.loads(raw.decode("utf-8"))
    except (binascii.Error, ValueError, UnicodeDecodeError) as exc:
        raise ApiError(ErrorCode.INVALID_CURSOR, detail="游标格式错误") from exc
    if not isinstance(data, dict) or not all(isinstance(v, str) for v in data.values()):
        raise ApiError(ErrorCode.INVALID_CURSOR, detail="游标内容非法")
    return data


@dataclass(slots=True)
class TradeRow:
    """insert_many 的输入行：除 user_id 外的持久化字段（标度整数）。"""

    trade_date: date
    security_code: str
    security_name: str
    side: str
    quantity_units: int
    price_units: int
    amount_units: int
    dedupe_key: str
    batch_id: str | None = None
    summary: str | None = None
    summary_code: str | None = None
    commission_units: int = 0
    stamp_tax_units: int = 0
    other_fee_units: int = 0
    cash_balance_units: int | None = None
    cash_balance_available: bool = False
    category: str | None = None
    occurrence_idx: int = 0
    account_masked: str | None = None
    note: str | None = None
    source: str = "import"
    line_no: int | None = None


def _trade_to_row(trade: Trade) -> TradeRow:
    """ORM 行 → 输入行（供回填/测试复用）。"""
    return TradeRow(
        trade_date=trade.trade_date,
        security_code=trade.security_code,
        security_name=trade.security_name,
        side=trade.side,
        quantity_units=trade.quantity_units,
        price_units=trade.price_units,
        amount_units=trade.amount_units,
        dedupe_key=trade.dedupe_key,
        batch_id=trade.batch_id,
    )


class TradeRepository:
    """明细仓储；构造只注入 session，所有查询强制带 user_id。"""

    __slots__ = ("_session",)

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ── 写入 ────────────────────────────────────────────────
    @staticmethod
    def _to_model(user_id: uuid.UUID, row: TradeRow) -> Trade:
        """TradeRow（标度整数）→ ORM 行；id/version 由系统生成。"""
        return Trade(
            id=new_trade_id(),
            user_id=user_id,
            batch_id=row.batch_id,
            trade_date=row.trade_date,
            security_code=row.security_code,
            security_name=row.security_name,
            summary=row.summary,
            summary_code=row.summary_code,
            side=row.side,
            quantity_units=row.quantity_units,
            price_units=row.price_units,
            amount_units=row.amount_units,
            commission_units=row.commission_units,
            stamp_tax_units=row.stamp_tax_units,
            other_fee_units=row.other_fee_units,
            cash_balance_units=row.cash_balance_units,
            cash_balance_available=row.cash_balance_available,
            category=row.category,
            dedupe_key=row.dedupe_key,
            occurrence_idx=row.occurrence_idx,
            account_masked=row.account_masked,
            note=row.note,
            source=row.source,
            version=1,
            line_no=row.line_no,
        )

    async def insert_many(self, user_id: uuid.UUID, rows: Sequence[TradeRow]) -> int:
        """批量插入（调用方需保证 dedupe_key 在同一 user 内不冲突）。"""
        if not rows:
            return 0
        models = [self._to_model(user_id, row) for row in rows]
        self._session.add_all(models)
        await self._session.flush()
        return len(models)

    async def insert_one(self, user_id: uuid.UUID, row: TradeRow) -> Trade:
        """插入单行并返回 ORM 行（对账补差需要回传 trade_id）。"""
        model = self._to_model(user_id, row)
        self._session.add(model)
        await self._session.flush()
        return model

    async def insert_one_or_existing(self, user_id: uuid.UUID, row: TradeRow) -> tuple[Trade, bool]:
        """插入单行；若同一去重键已有有效行则返回既有行（并发自愈用保存点兜住唯一冲突）。

        返回 `(行, 是否本次新插入)`：两个对账请求同时发现同一断链时，后到者复用先到者的
        补差行，而不是抛唯一键异常或重复补差。
        """
        savepoint = await self._session.begin_nested()
        try:
            model = await self.insert_one(user_id, row)
        except IntegrityError:
            await savepoint.rollback()
            existing = await self._session.scalar(
                select(Trade).where(
                    Trade.user_id == user_id,
                    Trade.dedupe_key == row.dedupe_key,
                    Trade.occurrence_idx == row.occurrence_idx,
                    Trade.deleted_at.is_(None),
                )
            )
            if existing is None:  # pragma: no cover - 非去重键冲突
                raise
            return existing, False
        await savepoint.commit()
        return model, True

    async def add_history(
        self,
        user_id: uuid.UUID,
        trade_id: str,
        *,
        version: int,
        op: str,
        snapshot: dict[str, Any],
        changed_fields: dict[str, Any],
        actor_id: uuid.UUID | None = None,
        at: datetime | None = None,
    ) -> None:
        """追加一条变更留痕；`at` 缺省用 DB 时间，调用方通常注入 clock。"""
        self._session.add(
            TradeHistory(
                trade_id=trade_id,
                user_id=user_id,
                version=version,
                op=op,
                snapshot=snapshot,
                changed_fields=changed_fields,
                actor_id=actor_id,
                **({"at": at} if at is not None else {}),
            )
        )
        await self._session.flush()

    async def update_fields(
        self,
        user_id: uuid.UUID,
        trade_id: str,
        *,
        expected_version: int,
        changes: dict[str, Any],
    ) -> Trade:
        """乐观锁更新：version 不匹配即 VERSION_CONFLICT（并发人工修正必争）。"""
        trade = await self.by_id(user_id, trade_id)
        if trade is None:
            raise NotFoundError(detail=f"交易 {trade_id} 不存在")
        if trade.version != expected_version:
            raise ApiError(ErrorCode.VERSION_CONFLICT, detail="版本已变化")
        unknown = set(changes) - _MUTABLE_FIELDS
        if unknown:
            raise ApiError(ErrorCode.VALIDATION_ERROR, detail=f"不可修改字段: {sorted(unknown)}")
        for key, value in changes.items():
            setattr(trade, key, value)
        trade.version += 1
        await self._session.flush()
        return trade

    async def soft_delete(self, user_id: uuid.UUID, trade_id: str, *, at: datetime) -> Trade:
        trade = await self.by_id(user_id, trade_id, include_deleted=True)
        if trade is None:
            raise NotFoundError(detail=f"交易 {trade_id} 不存在")
        trade.deleted_at = at
        trade.version += 1
        await self._session.flush()
        return trade

    async def restore(self, user_id: uuid.UUID, trade_id: str) -> Trade:
        trade = await self.by_id(user_id, trade_id, include_deleted=True)
        if trade is None:
            raise NotFoundError(detail=f"交易 {trade_id} 不存在")
        # 去重键只在有效行上唯一：若同键有效行已存在（例如撤销后又重新导入），
        # 直接恢复旧行会撞唯一索引；这里显式转 422，而不是把 DB 异常暴露成 500。
        live_conflict = await self._session.scalar(
            select(Trade.id).where(
                Trade.user_id == user_id,
                Trade.dedupe_key == trade.dedupe_key,
                Trade.occurrence_idx == trade.occurrence_idx,
                Trade.deleted_at.is_(None),
                Trade.id != trade.id,
            )
        )
        if live_conflict is not None:
            raise ApiError(
                ErrorCode.VALIDATION_ERROR,
                detail="同去重键的有效流水已存在（可能已重新导入），无法恢复该行",
            )
        trade.deleted_at = None
        trade.version += 1
        await self._session.flush()
        return trade

    async def soft_delete_by_batch(
        self,
        user_id: uuid.UUID,
        batch_id: str,
        *,
        at: datetime,
        only_version_1: bool = True,
    ) -> int:
        """撤销导入：只软删该批次写入、且未被人工改过（version=1）的行。"""
        trades = await self._session.scalars(
            select(Trade).where(
                Trade.user_id == user_id,
                Trade.batch_id == batch_id,
                Trade.deleted_at.is_(None),
            )
        )
        count = 0
        for trade in trades:
            if only_version_1 and trade.version != 1:
                continue
            trade.deleted_at = at
            trade.version += 1
            count += 1
        await self._session.flush()
        return count

    async def soft_delete_range(
        self, user_id: uuid.UUID, *, date_from: date, date_to: date, at: datetime
    ) -> int:
        """replace_range：软删区间内旧行，避免新账单与旧数据重复计入。"""
        trades = await self._session.scalars(
            select(Trade).where(
                Trade.user_id == user_id,
                Trade.trade_date >= date_from,
                Trade.trade_date <= date_to,
                Trade.deleted_at.is_(None),
            )
        )
        count = 0
        for trade in trades:
            trade.deleted_at = at
            trade.version += 1
            count += 1
        await self._session.flush()
        return count

    # ── 读取 ────────────────────────────────────────────────
    async def by_id(
        self, user_id: uuid.UUID, trade_id: str, *, include_deleted: bool = False
    ) -> Trade | None:
        stmt = select(Trade).where(Trade.user_id == user_id, Trade.id == trade_id)
        if not include_deleted:
            stmt = stmt.where(Trade.deleted_at.is_(None))
        return await self._session.scalar(stmt)

    async def page(
        self,
        user_id: uuid.UUID,
        *,
        cursor: str | None = None,
        limit: int,
        security_code: str | None = None,
        side: str | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
        category: str | None = None,
        include_deleted: bool = False,
    ) -> tuple[list[Trade], str | None]:
        """按 (trade_date DESC, id DESC) 游标翻页；多取 1 行判断是否还有下一页。"""
        capped = max(1, min(limit, CURSOR_MAX_LIMIT))
        stmt = select(Trade).where(Trade.user_id == user_id)
        if not include_deleted:
            stmt = stmt.where(Trade.deleted_at.is_(None))
        if security_code is not None:
            stmt = stmt.where(Trade.security_code == security_code)
        if side is not None:
            stmt = stmt.where(Trade.side == side)
        if category is not None:
            stmt = stmt.where(Trade.category == category)
        if date_from is not None:
            stmt = stmt.where(Trade.trade_date >= date_from)
        if date_to is not None:
            stmt = stmt.where(Trade.trade_date <= date_to)
        if cursor is not None:
            payload = decode_cursor(cursor)
            try:
                cursor_date = date.fromisoformat(payload["d"])
                cursor_id = payload["i"]
            except (KeyError, ValueError) as exc:
                raise ApiError(ErrorCode.INVALID_CURSOR, detail="游标字段缺失") from exc
            stmt = stmt.where(
                or_(
                    Trade.trade_date < cursor_date,
                    (Trade.trade_date == cursor_date) & (Trade.id < cursor_id),
                )
            )
        stmt = stmt.order_by(Trade.trade_date.desc(), Trade.id.desc()).limit(capped + 1)
        rows = list(await self._session.scalars(stmt))
        has_more = len(rows) > capped
        rows = rows[:capped]
        next_cursor = None
        if has_more and rows:
            last = rows[-1]
            next_cursor = encode_cursor({"d": last.trade_date.isoformat(), "i": last.id})
        return rows, next_cursor

    async def history(self, user_id: uuid.UUID, trade_id: str) -> list[TradeHistory]:
        rows = await self._session.scalars(
            select(TradeHistory)
            .where(TradeHistory.user_id == user_id, TradeHistory.trade_id == trade_id)
            .order_by(TradeHistory.version, TradeHistory.id)
        )
        return list(rows)

    async def balances_ordered(
        self,
        user_id: uuid.UUID,
        *,
        period_start: date | None = None,
        period_end: date | None = None,
    ) -> list[Trade]:
        """对账用的余额链：按业务发生顺序升序（日期 → 行号 → 补差行优先 → id）。

        对账补差行（side=missing）与断链行共用 line_no，必须排在断链行之前，
        这样 `上一行余额 + 补差金额 == 补差行余额` 与 `补差行余额 + 断链行金额 == 断链行余额`
        两条链式断言都能成立。
        """
        stmt = select(Trade).where(Trade.user_id == user_id, Trade.deleted_at.is_(None))
        if period_start is not None:
            stmt = stmt.where(Trade.trade_date >= period_start)
        if period_end is not None:
            stmt = stmt.where(Trade.trade_date <= period_end)
        stmt = stmt.order_by(
            Trade.trade_date.asc(),
            Trade.line_no.asc().nulls_first(),
            case((Trade.side == Side.MISSING.value, 0), else_=1).asc(),
            Trade.id.asc(),
        )
        return list(await self._session.scalars(stmt))

    async def list_by_batch(
        self,
        user_id: uuid.UUID,
        batch_id: str,
        *,
        side: str | None = None,
        include_deleted: bool = False,
        limit: int = 500,
    ) -> list[Trade]:
        """按批次列流水（补差行详情/审计用）；默认只看未删除行。"""
        stmt = select(Trade).where(Trade.user_id == user_id, Trade.batch_id == batch_id)
        if not include_deleted:
            stmt = stmt.where(Trade.deleted_at.is_(None))
        if side is not None:
            stmt = stmt.where(Trade.side == side)
        stmt = stmt.order_by(
            Trade.trade_date.asc(), Trade.line_no.asc().nulls_first(), Trade.id.asc()
        )
        stmt = stmt.limit(max(1, min(limit, 1000)))
        return list(await self._session.scalars(stmt))

    async def count_fills_by_batches(
        self, user_id: uuid.UUID, batch_ids: Sequence[str]
    ) -> dict[str, int]:
        """批量统计每个批次的系统补差行数（批次列表/详情的 filled_count）。"""
        ids = [batch_id for batch_id in batch_ids if batch_id]
        if not ids:
            return {}
        rows = await self._session.execute(
            select(Trade.batch_id, func.count())
            .where(
                Trade.user_id == user_id,
                Trade.batch_id.in_(ids),
                Trade.side == Side.MISSING.value,
                Trade.deleted_at.is_(None),
            )
            .group_by(Trade.batch_id)
        )
        return {str(batch_id): int(count) for batch_id, count in rows}

    async def count_by_filters(
        self,
        user_id: uuid.UUID,
        *,
        security_code: str | None = None,
        side: str | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
        category: str | None = None,
        include_deleted: bool = False,
    ) -> int:
        stmt = select(func.count()).select_from(Trade).where(Trade.user_id == user_id)
        if not include_deleted:
            stmt = stmt.where(Trade.deleted_at.is_(None))
        if security_code is not None:
            stmt = stmt.where(Trade.security_code == security_code)
        if side is not None:
            stmt = stmt.where(Trade.side == side)
        if category is not None:
            stmt = stmt.where(Trade.category == category)
        if date_from is not None:
            stmt = stmt.where(Trade.trade_date >= date_from)
        if date_to is not None:
            stmt = stmt.where(Trade.trade_date <= date_to)
        return int(await self._session.scalar(stmt) or 0)

    async def all_for_export(
        self,
        user_id: uuid.UUID,
        *,
        date_from: date | None = None,
        date_to: date | None = None,
        include_deleted: bool = False,
    ) -> list[Trade]:
        stmt = select(Trade).where(Trade.user_id == user_id)
        if not include_deleted:
            stmt = stmt.where(Trade.deleted_at.is_(None))
        if date_from is not None:
            stmt = stmt.where(Trade.trade_date >= date_from)
        if date_to is not None:
            stmt = stmt.where(Trade.trade_date <= date_to)
        stmt = stmt.order_by(Trade.trade_date.asc(), Trade.id.asc())
        return list(await self._session.scalars(stmt))

    async def existing_dedupe_keys(self, user_id: uuid.UUID, keys: Sequence[str]) -> set[str]:
        """批量查仍有效的去重键，供导入跳过重复行。

        只统计未软删的行：已撤销/被区间替换软删的行不能继续占着去重键，否则
        re-import 会把整批行静默 skip 掉（表现为“导入成功但流水为空”）。
        """
        if not keys:
            return set()
        rows = await self._session.scalars(
            select(Trade.dedupe_key).where(
                Trade.user_id == user_id,
                Trade.deleted_at.is_(None),
                Trade.dedupe_key.in_(keys),
            )
        )
        return set(rows)

    async def list_all(self, user_id: uuid.UUID) -> list[Trade]:
        """分类复算用：遍历用户全部未删除明细。"""
        return list(
            await self._session.scalars(
                select(Trade).where(Trade.user_id == user_id, Trade.deleted_at.is_(None))
            )
        )


def _utcnow() -> datetime:
    """兜底时钟（tz-aware UTC）；业务调用应优先注入 Clock。"""
    return datetime.now(tz=UTC)


__all__ = [
    "TradeRepository",
    "TradeRow",
    "decode_cursor",
    "encode_cursor",
]
