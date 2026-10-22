"""幂等键存储（API-Catalog §0.3 语义）。

同键同体 → 复用首响应；同键异体 → 422 IDEMPOTENCY_KEY_REUSED；处理中 → 409 + job_id。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from stock_contracts.clock import Clock
from stock_contracts.errors import ApiError, ErrorCode

from stock_platform.models import IdempotencyKey


class IdempotencyStore:
    __slots__ = ("_clock",)

    def __init__(self, clock: Clock) -> None:
        self._clock = clock

    async def begin(
        self,
        session: AsyncSession,
        *,
        user_id: uuid.UUID,
        endpoint: str,
        key: str,
        request_hash: str,
        ttl_seconds: int = 86400,
        job_id: str | None = None,
    ) -> IdempotencyKey | None:
        """返回 None 表示首次请求（继续执行）；否则表示应复用已有结果。"""
        now = self._clock.now()
        stmt = (
            pg_insert(IdempotencyKey)
            .values(
                user_id=user_id,
                endpoint=endpoint,
                key=key,
                request_hash=request_hash,
                expires_at=now + timedelta(seconds=ttl_seconds),
                job_id=job_id,
            )
            .on_conflict_do_nothing(constraint="uq_idempotency_user_endpoint_key")
            .returning(IdempotencyKey.id)
        )
        inserted = await session.scalar(stmt)
        if inserted is not None:
            return None

        existing = await session.scalar(
            select(IdempotencyKey).where(
                IdempotencyKey.user_id == user_id,
                IdempotencyKey.endpoint == endpoint,
                IdempotencyKey.key == key,
            )
        )
        if existing is None:  # pragma: no cover - 竞态兜底
            return None
        if existing.request_hash != request_hash:
            raise ApiError(ErrorCode.IDEMPOTENCY_KEY_REUSED)
        if existing.status_code is None:
            raise ApiError(
                ErrorCode.IDEMPOTENCY_IN_PROGRESS,
                metadata={"job_id": existing.job_id},
                detail="幂等键正在处理中",
            )
        return existing

    async def complete(
        self,
        session: AsyncSession,
        *,
        user_id: uuid.UUID,
        endpoint: str,
        key: str,
        status_code: int,
        body: dict[str, Any] | None = None,
        response_ref: str | None = None,
        job_id: str | None = None,
    ) -> None:
        record = await session.scalar(
            select(IdempotencyKey).where(
                IdempotencyKey.user_id == user_id,
                IdempotencyKey.endpoint == endpoint,
                IdempotencyKey.key == key,
            )
        )
        if record is None:  # pragma: no cover - 首请求即失败
            return
        record.status_code = status_code
        record.response_body = body
        record.response_ref = response_ref
        if job_id:
            record.job_id = job_id

    @staticmethod
    def payload_of(record: IdempotencyKey) -> dict[str, Any] | None:
        return record.response_body

    @staticmethod
    def status_of(record: IdempotencyKey) -> int:
        return record.status_code or 200

    @staticmethod
    def resolved_at(record: IdempotencyKey) -> datetime:
        return record.created_at
