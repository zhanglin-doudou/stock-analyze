"""用户与设置仓储（非租户表：平台级查询）。"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
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

    async def get_for_read(self, user_id: uuid.UUID) -> UserSetting | None:
        """只读：不落库、不懒创建（GET /settings 无行时返回 `None` 由 API 组默认视图）。"""
        return await self._session.scalar(select(UserSetting).where(UserSetting.user_id == user_id))

    async def update_patch(
        self,
        user_id: uuid.UUID,
        *,
        columns: dict[str, object],
        expected_version: int,
    ) -> UserSetting | None:
        """单条 CAS 更新：首次缺行先 `INSERT ... ON CONFLICT DO NOTHING`，版本不 +2。

        返回 `None` 表示版本冲突（调用方映射 409 VERSION_CONFLICT）。
        """
        await self._session.execute(
            pg_insert(UserSetting)
            .values(
                user_id=user_id,
                benchmark_id="hs300_tr",
                privacy_flags={},
                display_prefs={},
                version=1,
            )
            .on_conflict_do_nothing(index_elements=[UserSetting.user_id])
        )
        result = await self._session.execute(
            update(UserSetting)
            .where(UserSetting.user_id == user_id, UserSetting.version == expected_version)
            .values(**columns, version=UserSetting.version + 1, updated_at=func.now())
            .returning(UserSetting)
        )
        return result.scalar_one_or_none()
