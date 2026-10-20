"""明细应用层：列表（游标 + etag）、单条读取、修正（乐观锁）、软删/恢复、留痕。

修正走 If-Match 语义：调用方携带期望 version，仓储不匹配即 VERSION_CONFLICT。
每次写操作都同事务记 history + audit + outbox，保证“状态与留痕不脱节”。
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession
from stock_contracts.clock import Clock
from stock_contracts.constants import CURSOR_DEFAULT_LIMIT
from stock_contracts.errors import NotFoundError
from stock_core.values import Money, Price, Qty
from stock_platform.audit import record
from stock_platform.outbox import publish

from stock_ledger.models import Trade, TradeHistory
from stock_ledger.repositories.trades import TradeRepository

_EVENT_UPDATED = "ledger.trade.updated"
_EVENT_DELETED = "ledger.trade.deleted"
_EVENT_RESTORED = "ledger.trade.restored"


@dataclass(slots=True)
class TradeFilters:
    """列表/导出过滤条件。"""

    security_code: str | None = None
    side: str | None = None
    date_from: date | None = None
    date_to: date | None = None
    category: str | None = None
    include_deleted: bool = False


@dataclass(slots=True)
class TradeDTO:
    """明细返回体：内部标度整数 + 展示用定点字符串。"""

    id: str
    batch_id: str | None
    trade_date: date
    security_code: str
    security_name: str
    summary: str | None
    summary_code: str | None
    side: str
    category: str | None
    quantity_units: int
    price_units: int
    amount_units: int
    commission_units: int
    stamp_tax_units: int
    other_fee_units: int
    cash_balance_units: int | None
    cash_balance_available: bool
    occurrence_idx: int
    account_masked: str | None
    note: str | None
    source: str
    version: int
    line_no: int | None
    deleted_at: datetime | None
    created_at: datetime
    updated_at: datetime

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "batch_id": self.batch_id,
            "trade_date": self.trade_date.isoformat(),
            "security_code": self.security_code,
            "security_name": self.security_name,
            "summary": self.summary,
            "summary_code": self.summary_code,
            "side": self.side,
            "category": self.category,
            "quantity": str(Qty(self.quantity_units)),
            "price": str(Price(self.price_units)),
            "amount": str(Money(self.amount_units)),
            "commission": str(Money(self.commission_units)),
            "stamp_tax": str(Money(self.stamp_tax_units)),
            "other_fee": str(Money(self.other_fee_units)),
            "cash_balance": (
                str(Money(self.cash_balance_units)) if self.cash_balance_units is not None else None
            ),
            "cash_balance_available": self.cash_balance_available,
            "account_masked": self.account_masked,
            "note": self.note,
            "source": self.source,
            "version": self.version,
            "deleted_at": self.deleted_at.isoformat() if self.deleted_at else None,
        }


@dataclass(slots=True)
class TradeHistoryDTO:
    """变更留痕返回体。"""

    id: int
    version: int
    op: str
    snapshot: dict[str, Any]
    changed_fields: dict[str, Any]
    actor_id: uuid.UUID | None
    at: datetime


def trade_to_dto(trade: Trade) -> TradeDTO:
    return TradeDTO(
        id=trade.id,
        batch_id=trade.batch_id,
        trade_date=trade.trade_date,
        security_code=trade.security_code,
        security_name=trade.security_name,
        summary=trade.summary,
        summary_code=trade.summary_code,
        side=trade.side,
        category=trade.category,
        quantity_units=trade.quantity_units,
        price_units=trade.price_units,
        amount_units=trade.amount_units,
        commission_units=trade.commission_units,
        stamp_tax_units=trade.stamp_tax_units,
        other_fee_units=trade.other_fee_units,
        cash_balance_units=trade.cash_balance_units,
        cash_balance_available=trade.cash_balance_available,
        occurrence_idx=trade.occurrence_idx,
        account_masked=trade.account_masked,
        note=trade.note,
        source=trade.source,
        version=trade.version,
        line_no=trade.line_no,
        deleted_at=trade.deleted_at,
        created_at=trade.created_at,
        updated_at=trade.updated_at,
    )


def trade_snapshot(trade: Trade) -> dict[str, Any]:
    """整行快照（标度整数），供 history 回滚定位。"""
    return {
        "id": trade.id,
        "user_id": str(trade.user_id),
        "batch_id": trade.batch_id,
        "trade_date": trade.trade_date.isoformat(),
        "security_code": trade.security_code,
        "security_name": trade.security_name,
        "summary": trade.summary,
        "summary_code": trade.summary_code,
        "side": trade.side,
        "quantity_units": trade.quantity_units,
        "price_units": trade.price_units,
        "amount_units": trade.amount_units,
        "commission_units": trade.commission_units,
        "stamp_tax_units": trade.stamp_tax_units,
        "other_fee_units": trade.other_fee_units,
        "cash_balance_units": trade.cash_balance_units,
        "category": trade.category,
        "account_masked": trade.account_masked,
        "note": trade.note,
        "version": trade.version,
        "deleted_at": trade.deleted_at.isoformat() if trade.deleted_at else None,
    }


def _etag(rows: list[Trade]) -> str:
    payload = ",".join(f"{row.id}:{row.version}" for row in rows)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


async def list_trades(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    cursor: str | None = None,
    limit: int = CURSOR_DEFAULT_LIMIT,
    filters: TradeFilters | None = None,
) -> tuple[list[TradeDTO], str | None, str]:
    """游标分页 + 结果集 etag（供 If-None-Match 命中缓存）。"""
    spec = filters or TradeFilters()
    rows, next_cursor = await TradeRepository(session).page(
        user_id,
        cursor=cursor,
        limit=limit,
        security_code=spec.security_code,
        side=spec.side,
        date_from=spec.date_from,
        date_to=spec.date_to,
        category=spec.category,
        include_deleted=spec.include_deleted,
    )
    return [trade_to_dto(row) for row in rows], next_cursor, _etag(rows)


async def get_trade(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    trade_id: str,
    include_deleted: bool = False,
) -> TradeDTO:
    trade = await TradeRepository(session).by_id(user_id, trade_id, include_deleted=include_deleted)
    if trade is None:
        raise NotFoundError(detail=f"交易 {trade_id} 不存在")
    return trade_to_dto(trade)


async def patch_trade(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    trade_id: str,
    changes: dict[str, Any],
    expected_version: int,
    clock: Clock,
    actor_id: uuid.UUID | None = None,
    request_id: str | None = None,
) -> TradeDTO:
    """人工修正：If-Match 版本校验 → 更新 → 记 changed_fields 留痕 → 审计/事件。"""
    repo = TradeRepository(session)
    before = await repo.by_id(user_id, trade_id)
    if before is None:
        raise NotFoundError(detail=f"交易 {trade_id} 不存在")
    snapshot_before = trade_snapshot(before)

    trade = await repo.update_fields(
        user_id, trade_id, expected_version=expected_version, changes=changes
    )
    changed = {
        key: {"from": snapshot_before.get(key), "to": value} for key, value in changes.items()
    }
    await repo.add_history(
        user_id,
        trade_id,
        version=trade.version,
        op="update",
        snapshot=trade_snapshot(trade),
        changed_fields=changed,
        actor_id=actor_id,
        at=clock.now(),
    )
    await record(
        session,
        action="trade.update",
        actor_id=actor_id or user_id,
        target_type="trade",
        target_id=trade_id,
        meta={"fields": sorted(changes)},
        request_id=request_id,
    )
    await publish(
        session,
        event_type=_EVENT_UPDATED,
        aggregate="trade",
        aggregate_id=trade_id,
        user_id=user_id,
        payload={"version": trade.version, "fields": sorted(changes)},
    )
    return trade_to_dto(trade)


async def delete_trade(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    trade_id: str,
    clock: Clock,
    actor_id: uuid.UUID | None = None,
    request_id: str | None = None,
) -> TradeDTO:
    repo = TradeRepository(session)
    trade = await repo.soft_delete(user_id, trade_id, at=clock.now())
    deleted_iso = trade.deleted_at.isoformat() if trade.deleted_at is not None else None
    await repo.add_history(
        user_id,
        trade_id,
        version=trade.version,
        op="delete",
        snapshot=trade_snapshot(trade),
        changed_fields={"deleted_at": {"from": None, "to": deleted_iso}},
        actor_id=actor_id,
        at=clock.now(),
    )
    await record(
        session,
        action="trade.delete",
        actor_id=actor_id or user_id,
        target_type="trade",
        target_id=trade_id,
        request_id=request_id,
    )
    await publish(
        session,
        event_type=_EVENT_DELETED,
        aggregate="trade",
        aggregate_id=trade_id,
        user_id=user_id,
        payload={"version": trade.version},
    )
    return trade_to_dto(trade)


async def restore_trade(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    trade_id: str,
    clock: Clock,
    actor_id: uuid.UUID | None = None,
    request_id: str | None = None,
) -> TradeDTO:
    repo = TradeRepository(session)
    trade = await repo.restore(user_id, trade_id)
    await repo.add_history(
        user_id,
        trade_id,
        version=trade.version,
        op="restore",
        snapshot=trade_snapshot(trade),
        changed_fields={"deleted_at": {"from": "<deleted>", "to": None}},
        actor_id=actor_id,
        at=clock.now(),
    )
    await record(
        session,
        action="trade.restore",
        actor_id=actor_id or user_id,
        target_type="trade",
        target_id=trade_id,
        request_id=request_id,
    )
    await publish(
        session,
        event_type=_EVENT_RESTORED,
        aggregate="trade",
        aggregate_id=trade_id,
        user_id=user_id,
        payload={"version": trade.version},
    )
    return trade_to_dto(trade)


async def history(
    session: AsyncSession, *, user_id: uuid.UUID, trade_id: str
) -> list[TradeHistoryDTO]:
    rows: list[TradeHistory] = await TradeRepository(session).history(user_id, trade_id)
    return [
        TradeHistoryDTO(
            id=row.id,
            version=row.version,
            op=row.op,
            snapshot=row.snapshot,
            changed_fields=row.changed_fields,
            actor_id=row.actor_id,
            at=row.at,
        )
        for row in rows
    ]


__all__ = [
    "TradeDTO",
    "TradeFilters",
    "TradeHistoryDTO",
    "delete_trade",
    "get_trade",
    "history",
    "list_trades",
    "patch_trade",
    "restore_trade",
    "trade_snapshot",
    "trade_to_dto",
]
