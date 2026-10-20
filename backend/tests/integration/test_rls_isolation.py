"""双层隔离直连库验证（Architecture §14.3）。

用 `stock_app`（非 owner、NOBYPASSRLS）角色 + `SET LOCAL app.user_id`，验证 RLS 真正生效：
  (a) A 的租户会话读不到 B 的行；
  (b) 未设置 `app.user_id` 时读到 0 行（默认拒绝）；
  (c) 连接归还连接池后再取出的连接无残留 `app.user_id`（防串号）；
  (d) `stock_app` 不能执行 DDL（最小权限）。
"""

from __future__ import annotations

import uuid
from datetime import date

import pytest
from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from stock_platform.config import Settings

pytestmark = [pytest.mark.integration, pytest.mark.isolation]


async def _seed_trade(engine: AsyncEngine, user_id: uuid.UUID, code: str) -> None:
    """以 owner 直插一条明细（owner 不受 RLS 约束，用于准备两侧数据）。"""
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "insert into ledger.trade (id, user_id, trade_date, security_code, "
                "security_name, side, quantity_units, price_units, amount_units, "
                "commission_units, stamp_tax_units, other_fee_units, "
                "cash_balance_available, occurrence_idx, source, version, dedupe_key) "
                "values (:id, :uid, :d, :code, :name, 'buy', 1000000, 1000000, 1000000, "
                "0, 0, 0, false, 0, 'import', 1, :dk)"
            ),
            {
                "id": f"trd_{uuid.uuid4().hex[:12]}",
                "uid": user_id,
                "d": date(2026, 6, 30),
                "code": code,
                "name": code,
                "dk": f"{code}:{uuid.uuid4().hex[:8]}",
            },
        )


async def _scalar(engine: AsyncEngine, sql: str, params: dict[str, object]) -> object:
    async with engine.begin() as conn:
        return await conn.scalar(text(sql), params)


async def _count_visible(engine: AsyncEngine, user_id: uuid.UUID | None) -> int:
    """在租户会话内统计可见明细行数。"""
    async with engine.begin() as conn:
        if user_id is not None:
            await conn.execute(
                text("select set_config('app.user_id', :uid, true)"), {"uid": str(user_id)}
            )
        return int(
            await conn.scalar(text("select count(*) from ledger.trade"))  # type: ignore[arg-type]
        )


@pytest.fixture
async def two_users(owner_engine: AsyncEngine) -> tuple[uuid.UUID, uuid.UUID]:
    user_a = uuid.uuid4()
    user_b = uuid.uuid4()
    # 用户行本身在 platform.user，RLS 也开着；owner 直插两条用户
    async with owner_engine.begin() as conn:
        for uid in (user_a, user_b):
            await conn.execute(
                text(
                    "insert into platform.user (id, email, password_hash, role, status) "
                    "values (:id, :email, 'x', 'user', 'active')"
                ),
                {"id": uid, "email": f"{uid.hex[:12]}@example.com"},
            )
    await _seed_trade(owner_engine, user_a, "600519")
    await _seed_trade(owner_engine, user_a, "000001")
    await _seed_trade(owner_engine, user_b, "300750")
    return user_a, user_b


async def test_a_session_cannot_see_b_rows(
    app_engine: AsyncEngine, two_users: tuple[uuid.UUID, uuid.UUID]
) -> None:
    user_a, user_b = two_users
    assert await _count_visible(app_engine, user_a) == 2
    assert await _count_visible(app_engine, user_b) == 1


async def test_unset_tenant_reads_zero_rows(
    app_engine: AsyncEngine, two_users: tuple[uuid.UUID, uuid.UUID]
) -> None:
    assert await _count_visible(app_engine, None) == 0


async def test_pool_checkout_has_no_leftover_tenant(settings: Settings) -> None:
    """串号测试：pool_size=1 强制复用同一物理连接。"""
    engine = create_async_engine(settings.app_dsn(), pool_size=1, max_overflow=0)
    try:
        first = uuid.uuid4()
        async with engine.begin() as conn:
            await conn.execute(
                text("select set_config('app.user_id', :uid, true)"), {"uid": str(first)}
            )
            assert await conn.scalar(text("select current_setting('app.user_id', true)")) == str(
                first
            )
        # 归还后再次取出同一连接：SET LOCAL 已随事务结束失效
        async with engine.begin() as conn:
            leftover = await conn.scalar(text("select current_setting('app.user_id', true)"))
        assert leftover in (None, "")
    finally:
        await engine.dispose()


async def test_app_role_cannot_execute_ddl(app_engine: AsyncEngine) -> None:
    with pytest.raises(ProgrammingError):
        async with app_engine.begin() as conn:
            await conn.execute(text("create table ledger.ddl_probe (id int)"))
