"""用户与设置仓储（非租户表：平台级查询）。"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from stock_contracts.enums import Role

from stock_platform.models import User, UserSetting


class UserRepository:
    __slots__ = ("_session",)

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def by_email(self, email: str) -> User | None:
        return await self._session.scalar(select(User).where(User.email == email))

    async def by_id(self, user_id: uuid.UUID) -> User | None:
        return await self._session.scalar(select(User).where(User.id == user_id))

    async def create(
        self,
        *,
        email: str,
        password_hash: str,
        role: Role = Role.USER,
        display_name: str | None = None,
    ) -> User:
        user = User(
            email=email,
            password_hash=password_hash,
            role=str(role),
            display_name=display_name,
            status="active",
        )
        self._session.add(user)
        await self._session.flush()
        return user

    async def mark_login(self, user: User) -> None:  # pragma: no cover - 预留
        user.updated_at = datetime.now(tz=UTC)

    async def soft_delete(self, user_id: uuid.UUID) -> None:
        user = await self.by_id(user_id)
        if user is not None:
            user.status = "deleted"
            user.deleted_at = datetime.now(tz=UTC)

    async def hard_delete(self, user_id: uuid.UUID) -> None:
        """FR-A6：物理删除该账号全部业务数据（调用方负责先删业务表）。"""
        await self._session.execute(delete(User).where(User.id == user_id))


class UserSettingRepository:
    __slots__ = ("_session",)

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, user_id: uuid.UUID) -> UserSetting:
        setting = await self._session.scalar(
            select(UserSetting).where(UserSetting.user_id == user_id)
        )
        if setting is None:
            setting = UserSetting(user_id=user_id)
            self._session.add(setting)
            await self._session.flush()
        return setting
