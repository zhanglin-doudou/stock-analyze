"""Outbox 模式（Architecture §8）：与业务同事务落库，异步发布。"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from stock_platform.models import OutboxEvent


async def publish(
    session: AsyncSession,
    *,
    event_type: str,
    aggregate: str,
    aggregate_id: str | None = None,
    user_id: uuid.UUID | None = None,
    payload: dict[str, Any] | None = None,
) -> None:
    """同一事务内写入；不会因下游故障丢事件。"""
    session.add(
        OutboxEvent(
            type=event_type,
            aggregate=aggregate,
            aggregate_id=aggregate_id,
            user_id=user_id,
            payload=payload or {},
        )
    )


async def fetch_unpublished(session: AsyncSession, *, limit: int = 100) -> Sequence[OutboxEvent]:
    rows = await session.scalars(
        select(OutboxEvent)
        .where(OutboxEvent.published_at.is_(None))
        .order_by(OutboxEvent.occurred_at, OutboxEvent.id)
        .limit(limit)
        .with_for_update(skip_locked=True)
    )
    return list(rows)


async def mark_published(session: AsyncSession, event_id: int) -> None:
    event = await session.get(OutboxEvent, event_id)
    if event is not None:
        event.published_at = datetime.now(tz=UTC)
        event.attempts += 1
