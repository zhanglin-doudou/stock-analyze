"""Alembic 迁移环境（多 schema：platform / ledger / review / delivery）。

- DSN 取自 `Settings.owner_dsn()`（stock_owner：schema/表 owner，可 DDL）。
- `include_schemas=True`：跨 schema 比对；只关心自有 4 个 schema。
- 迁移用 asyncpg + `connection.run_sync`，不引入同步驱动。
"""

from __future__ import annotations

import asyncio
import contextlib
from logging.config import fileConfig
from typing import Any

import stock_ledger.models  # noqa: F401

# 让 Base.metadata 收集到全部业务表；缺失的包（如 review 尚未落地）按需忽略。
import stock_platform.models  # noqa: F401
from alembic import context
from sqlalchemy.ext.asyncio import create_async_engine
from stock_platform.config import get_settings
from stock_platform.db import Base

for _optional in ("stock_review.models", "stock_delivery.models"):
    with contextlib.suppress(ImportError):  # 可选包尚未落地
        __import__(_optional)

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

OWNED_SCHEMAS = ("platform", "ledger", "review", "delivery")


def _include_object(
    obj: Any, name: str | None, type_: str, reflected: bool, compare_to: Any
) -> bool:
    """只迁移自有 schema，忽略 public / 扩展对象。"""
    if type_ == "table":
        schema = getattr(obj, "schema", None)
        if schema is not None and schema not in OWNED_SCHEMAS:
            return False
    return True


def _dsn() -> str:
    return get_settings().owner_dsn()


def run_migrations_offline() -> None:
    context.configure(
        url=_dsn(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        include_schemas=True,
        compare_type=True,
        include_object=_include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


def _do_run_migrations(connection: Any) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        include_schemas=True,
        compare_type=True,
        include_object=_include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


async def _run_async_migrations() -> None:
    engine = create_async_engine(_dsn(), pool_pre_ping=True)
    async with engine.connect() as connection:
        await connection.run_sync(_do_run_migrations)
    await engine.dispose()


def run_migrations_online() -> None:
    asyncio.run(_run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
