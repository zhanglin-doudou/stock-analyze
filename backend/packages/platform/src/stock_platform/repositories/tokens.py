"""凭证仓储：refresh token 轮换族、PAT。"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from stock_platform.models import ApiToken, RefreshToken


class RefreshTokenRepository:
    __slots__ = ("_session",)

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def by_hash(self, token_hash: str) -> RefreshToken | None:
        return await self._session.scalar(
            select(RefreshToken).where(RefreshToken.token_hash == token_hash)
        )

    async def issue(
        self,
        *,
        user_id: uuid.UUID,
        token_hash: str,
        ttl_seconds: int,
        family_id: uuid.UUID | None = None,
        device_name: str | None = None,
        now: datetime | None = None,
    ) -> RefreshToken:
        issued = now or datetime.now(tz=UTC)
        record = RefreshToken(
            user_id=user_id,
            token_hash=token_hash,
            family_id=family_id or uuid.uuid4(),
            device_name=device_name,
            expires_at=issued + timedelta(seconds=ttl_seconds),
        )
        self._session.add(record)
        await self._session.flush()
        return record

    async def revoke(self, record: RefreshToken, *, now: datetime | None = None) -> None:
        record.revoked_at = now or datetime.now(tz=UTC)

    async def revoke_family(self, family_id: uuid.UUID, *, now: datetime | None = None) -> int:
        """令牌复用检测：整族撤销（API-005 断言②）。"""
        stamp = now or datetime.now(tz=UTC)
        rows = await self._session.scalars(
            select(RefreshToken).where(
                RefreshToken.family_id == family_id, RefreshToken.revoked_at.is_(None)
            )
        )
        count = 0
        for row in rows:
            row.revoked_at = stamp
            count += 1
        return count

    async def revoke_all_for_user(self, user_id: uuid.UUID, *, now: datetime | None = None) -> int:
        stamp = now or datetime.now(tz=UTC)
        rows = await self._session.scalars(
            select(RefreshToken).where(
                RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None)
            )
        )
        count = 0
        for row in rows:
            row.revoked_at = stamp
            count += 1
        return count


class ApiTokenRepository:
    __slots__ = ("_session",)

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def by_hash(self, token_hash: str) -> ApiToken | None:
        return await self._session.scalar(select(ApiToken).where(ApiToken.token_hash == token_hash))

    async def list_for_user(self, user_id: uuid.UUID) -> list[ApiToken]:
        rows = await self._session.scalars(
            select(ApiToken).where(ApiToken.user_id == user_id).order_by(ApiToken.created_at)
        )
        return list(rows)

    async def by_id(self, user_id: uuid.UUID, token_id: str) -> ApiToken | None:
        return await self._session.scalar(
            select(ApiToken).where(ApiToken.user_id == user_id, ApiToken.id == token_id)
        )

    async def create(
        self,
        *,
        token_id: str,
        user_id: uuid.UUID,
        name: str,
        token_prefix: str,
        token_hash: str,
        scopes: list[str],
        ttl_seconds: int,
        now: datetime | None = None,
    ) -> ApiToken:
        issued = now or datetime.now(tz=UTC)
        record = ApiToken(
            id=token_id,
            user_id=user_id,
            name=name,
            token_prefix=token_prefix,
            token_hash=token_hash,
            scopes=scopes,
            expires_at=issued + timedelta(seconds=ttl_seconds),
        )
        self._session.add(record)
        await self._session.flush()
        return record

    async def revoke(self, record: ApiToken, *, now: datetime | None = None) -> None:
        record.revoked_at = now or datetime.now(tz=UTC)

    async def touch(self, record: ApiToken, *, now: datetime | None = None) -> None:
        record.last_used_at = now or datetime.now(tz=UTC)
