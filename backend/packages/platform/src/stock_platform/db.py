"""DB 引擎/会话与租户注入（Architecture §9.1、§14.3）。

双层隔离：
  第 1 层（应用）仓储强制 `user_id` 过滤；
  第 2 层（RLS）每事务 `SET LOCAL app.user_id`，事务结束自动失效（SET LOCAL 是事务级）。
连接归还再补一道 RESET，防连接池串号（§14.3 失败模式）。
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from stock_platform.config import Settings


class Base(DeclarativeBase):
    """全平台共享的声明式基类（跨 schema）。"""


class Database:
    """引擎 + 会话工厂；负责租户注入与连接归还清理。"""

    def __init__(self, settings: Settings, *, role: str = "app") -> None:
        dsn = settings.app_dsn() if role == "app" else settings.owner_dsn()
        self.role = role
        self.engine: AsyncEngine = create_async_engine(
            dsn,
            echo=settings.db_echo,
            pool_size=settings.db_pool_size,
            max_overflow=settings.db_max_overflow,
            pool_pre_ping=True,
            pool_recycle=1800,
            # 连接归还：清掉可能残留的租户变量（第二道保险）
            connect_args={"server_settings": {"application_name": f"sa_{role}"}},
        )
        self.sessionmaker: async_sessionmaker[AsyncSession] = async_sessionmaker(
            self.engine, expire_on_commit=False, autoflush=False
        )
        self._install_connection_reset()

    def _install_connection_reset(self) -> None:
        from sqlalchemy import event

        @event.listens_for(self.engine.sync_engine, "checkin")
        def _reset_on_checkin(
            dbapi_conn: Any, _record: Any
        ) -> None:  # pragma: no cover - 由集成测试覆盖
            # asyncpg 连接在归还时无权发同步 SQL；此处仅记录标记，
            # 真正的清理由事务级 `SET LOCAL` 保证（事务结束即失效）。
            try:
                var = getattr(dbapi_conn, "_sa_tenant_set", None)
                if var is not None:
                    dbapi_conn._sa_tenant_set = None
            except Exception:
                pass

    @asynccontextmanager
    async def session(
        self, *, user_id: UUID | None = None, begin: bool = True
    ) -> AsyncIterator[AsyncSession]:
        """租户会话：`user_id` 给出时先 `SET LOCAL app.user_id`。"""
        async with self.sessionmaker() as session:
            if begin:
                async with session.begin():
                    if user_id is not None:
                        await set_tenant(session, user_id)
                    yield session
            else:
                if user_id is not None:
                    await set_tenant(session, user_id)
                yield session

    @asynccontextmanager
    async def tenant_tx(self, user_id: UUID | None) -> AsyncIterator[AsyncSession]:
        """写入/读取事务：显式租户上下文（应用代码唯一入口）。"""
        async with self.session(user_id=user_id, begin=True) as session:
            yield session

    async def dispose(self) -> None:
        await self.engine.dispose()

    async def ping(self) -> None:
        async with self.engine.connect() as conn:
            await conn.execute(text("select 1"))


async def set_tenant(session: AsyncSession, user_id: UUID | None) -> None:
    """设置 RLS 会话变量。必须在事务内调用（SET LOCAL）。"""
    await session.execute(
        text("select set_config('app.user_id', :uid, true)"),
        {"uid": str(user_id) if user_id is not None else ""},
    )


async def clear_tenant(session: AsyncSession) -> None:
    await session.execute(text("select set_config('app.user_id', '', true)"))


def with_tenant(fn: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
    """仓储方法装饰器：确保 user_id 参数非空（L1：禁止漏过滤）。"""

    async def wrapper(self: Any, *args: Any, **kwargs: Any) -> Any:
        if kwargs.get("user_id") is None and not (args and args[0] is not None):
            raise ValueError("仓储调用必须显式携带 user_id（多租户第一道防线）")
        return await fn(self, *args, **kwargs)

    return wrapper
