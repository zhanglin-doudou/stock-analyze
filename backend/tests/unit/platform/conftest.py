"""unit/platform 共享夹具（G2 队列语义）：真实 PostgreSQL + 独立测试库。

库名由环境变量 `STOCK_DB_NAME` 决定（验收用 `stock_p2_g2`），不复用默认库，
避免与其它并行验收的 TRUNCATE 互相抢锁。
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine
from stock_contracts.clock import FrozenClock
from stock_platform.config import Settings
from stock_platform.db import Database
from stock_platform.jobs import JobQueue


@pytest_asyncio.fixture
async def db(settings: Settings) -> AsyncIterator[Database]:
    """队列控制面数据库（owner 角色）。

    G7 起 `platform.job` 启用 RLS；unit 用例直接驱动的是 **worker 控制面**
    （claim/reap/heartbeat 跨租户动作，生产同样走 owner Database），因此这里
    用 owner 角色。租户面（app + SET LOCAL）的 job 可见性由
    `backend/tests/integration/test_platform_rls.py` 覆盖。
    """
    database = Database(settings, role="owner")
    try:
        yield database
    finally:
        await database.dispose()


@pytest.fixture
def clock() -> FrozenClock:
    """冻结时钟：租约/回收的时间推进完全确定。

    +1s 是为了保证 `job.available_at`（server_default=now()）不晚于 `clock.now()`，
    否则刚入队的任务 claim 不到。
    """
    return FrozenClock(datetime.now(tz=UTC) + timedelta(seconds=1))


@pytest.fixture
def queue(db: Database, clock: FrozenClock) -> JobQueue:
    return JobQueue(db, clock)


@pytest_asyncio.fixture
async def user_id(owner_engine: AsyncEngine) -> uuid.UUID:
    """owner 直插用户（job.user_id 外键需要），绕过注册/登录链路。"""
    uid = uuid.uuid4()
    async with owner_engine.begin() as conn:
        await conn.execute(
            text(
                "insert into platform.user (id, email, password_hash, role, status) "
                "values (:id, :email, 'hash', 'user', 'active')"
            ),
            {"id": uid, "email": f"g2-{uid.hex[:12]}@example.com"},
        )
    return uid
