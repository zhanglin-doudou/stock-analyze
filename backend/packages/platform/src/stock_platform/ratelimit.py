"""限速计数器（PG 窗口桶，Architecture §14.4）。

本地单实例即可运行；键空间按 IP/账号/令牌隔离。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from stock_contracts.clock import Clock

from stock_platform.models import LoginAttempt, RateLimitBucket


class RateLimiter:
    __slots__ = ("_clock",)

    def __init__(self, clock: Clock) -> None:
        self._clock = clock

    def _window_start(self, window_seconds: int) -> datetime:
        now = self._clock.now()
        epoch = int(now.timestamp())
        floored = epoch - (epoch % window_seconds)
        return datetime.fromtimestamp(floored, tz=UTC)

    async def hit(
        self, session: AsyncSession, key: str, *, limit: int, window_seconds: int
    ) -> tuple[bool, int]:
        """返回 (是否允许, Retry-After 秒)。计数在事务内自增。"""
        window = self._window_start(window_seconds)
        stmt = (
            pg_insert(RateLimitBucket)
            .values(key=key, window_start=window, count=1)
            .on_conflict_do_update(
                index_elements=[RateLimitBucket.key, RateLimitBucket.window_start],
                set_={"count": RateLimitBucket.count + 1},
            )
            .returning(RateLimitBucket.count)
        )
        count = await session.scalar(stmt)
        assert count is not None
        retry_after = window_seconds - int((self._clock.now() - window).total_seconds())
        return count <= limit, max(retry_after, 1)

    async def peek(self, session: AsyncSession, key: str, *, window_seconds: int) -> int:
        window = self._window_start(window_seconds)
        count = await session.scalar(
            select(RateLimitBucket.count).where(
                RateLimitBucket.key == key, RateLimitBucket.window_start == window
            )
        )
        return int(count or 0)

    # ── 登录失败计数（独立于通用桶，便于审计）───────────────
    async def record_login_attempt(
        self, session: AsyncSession, *, email: str, ip_hash: str | None, success: bool
    ) -> int:
        session.add(LoginAttempt(email=email, ip_hash=ip_hash, success=success))
        await session.flush()
        if success:
            return 0
        since = self._clock.now() - timedelta(seconds=300)
        rows = await session.scalars(
            select(LoginAttempt.id).where(
                LoginAttempt.email == email,
                LoginAttempt.success.is_(False),
                LoginAttempt.at >= since,
            )
        )
        return len(list(rows))


__all__ = ["RateLimiter"]
