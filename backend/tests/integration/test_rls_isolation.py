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


# ── review 六表 RLS（Period2-02/03/16，迁移 0003/0004/0006；gate 要求 review 6/6）──

_REVIEW_TABLES = (
    "round",
    "round_fill",
    "round_build_state",
    "nav_daily",
    "asset_daily",
    "asset_build_state",
)

_INSERT_ROUND_SQL = """
insert into review.round (
    id, user_id, security_code, security_name, status, open_trade_id, close_trade_id,
    open_date, close_date, quantity_units, cost_units, proceeds_units, dividend_units,
    pnl_units, return_rate_units, holding_days, data_error, oversell_units,
    fills_count, rebuilt_at
) values (
    :id, :uid, '600519', '贵州茅台', 'closed', :open_trade_id, :close_trade_id,
    :open_date, :close_date, 1000000, 1000000, 1200000, 0,
    200000, 20000000, 5, false, 0,
    1, now()
)
"""

_INSERT_FILL_SQL = """
insert into review.round_fill (
    user_id, round_id, sequence, buy_trade_id, sell_trade_id,
    quantity_units, buy_cost_units, sell_proceeds_units, dividend_units, pnl_units,
    open_date, close_date, holding_days
) values (
    :uid, :round_id, 1, :buy_trade_id, :sell_trade_id,
    1000000, 1000000, 1200000, 0, 200000,
    :open_date, :close_date, 5
)
"""

_INSERT_BUILD_STATE_SQL = """
insert into review.round_build_state
    (user_id, last_rebuilt_at, last_scope, rounds_count, version)
values (:uid, now(), 'full', 1, 1)
"""

_INSERT_NAV_DAILY_SQL = """
insert into review.nav_daily (
    id, user_id, trade_date, nav_units, return_units, total_value_units,
    cashflow_units, shares_units, status, blocked_reason, rebuilt_at
) values (
    :nav_id, :uid, :open_date, 1000000, 0, 1000000,
    0, 100000000, 'ok', null, now()
)
"""

_INSERT_ASSET_DAILY_SQL = """
insert into review.asset_daily (
    id, user_id, trade_date, cash_units, market_value_units, total_assets_units,
    holding_cost_units, unrealized_pnl_units, holding_count, cashflow_units,
    prev_total_assets_units, daily_pnl_units, daily_return_units, status,
    status_reason, definition_version, rebuilt_at
) values (
    :asset_id, :uid, :open_date, 1000000, 0, 1000000,
    0, 0, 0, 0,
    1000000, 0, 0, 'ok',
    null, '1.0', now()
)
"""

_INSERT_ASSET_BUILD_STATE_SQL = """
insert into review.asset_build_state (
    user_id, start_date, last_built_through, last_built_at, last_trade_updated_at,
    last_trade_count, last_trade_version_sum, data_as_of, assumption, last_status,
    last_fifo_error, status_reason, last_mode, last_rebuild_from, last_rows_written,
    definition_version, rows_count, version
) values (
    :uid, :open_date, :open_date, now(), now(),
    1, 1, :open_date, 'back_solved', 'ok',
    false, null, 'full', :open_date, 1,
    '1.0', 1, 1
)
"""


async def _seed_review_rows(engine: AsyncEngine, user_id: uuid.UUID) -> str:
    """以 owner 直插 review 六表各一行（绕过 RLS，为两侧租户准备数据）。"""
    round_id = f"rnd_{uuid.uuid4().hex[:32]}"
    trade_ids = {
        "id": round_id,
        "uid": user_id,
        "open_trade_id": f"trd_{uuid.uuid4().hex[:12]}",
        "close_trade_id": f"trd_{uuid.uuid4().hex[:12]}",
        "open_date": date(2026, 1, 5),
        "close_date": date(2026, 1, 7),
        "buy_trade_id": f"trd_{uuid.uuid4().hex[:12]}",
        "sell_trade_id": f"trd_{uuid.uuid4().hex[:12]}",
        "round_id": round_id,
    }
    async with engine.begin() as conn:
        await conn.execute(text(_INSERT_ROUND_SQL), trade_ids)
        await conn.execute(text(_INSERT_FILL_SQL), trade_ids)
        await conn.execute(text(_INSERT_BUILD_STATE_SQL), {"uid": user_id})
        await conn.execute(
            text(_INSERT_NAV_DAILY_SQL),
            {"nav_id": f"nav_{uuid.uuid4().hex[:32]}", "uid": user_id, **trade_ids},
        )
        await conn.execute(
            text(_INSERT_ASSET_DAILY_SQL),
            {"asset_id": f"ast_{uuid.uuid4().hex[:32]}", "uid": user_id, **trade_ids},
        )
        await conn.execute(text(_INSERT_ASSET_BUILD_STATE_SQL), {"uid": user_id, **trade_ids})
    return round_id


async def _count_table_visible(engine: AsyncEngine, user_id: uuid.UUID | None, table: str) -> int:
    """在租户会话内统计 review 表可见行数（table 来自测试常量，无注入面）。"""
    assert table in _REVIEW_TABLES
    async with engine.begin() as conn:
        if user_id is not None:
            await conn.execute(
                text("select set_config('app.user_id', :uid, true)"), {"uid": str(user_id)}
            )
        return int(
            await conn.scalar(text(f"select count(*) from review.{table}"))  # type: ignore[arg-type]
        )


@pytest.fixture
async def review_seeded(
    owner_engine: AsyncEngine, two_users: tuple[uuid.UUID, uuid.UUID]
) -> tuple[uuid.UUID, uuid.UUID]:
    user_a, user_b = two_users
    await _seed_review_rows(owner_engine, user_a)
    await _seed_review_rows(owner_engine, user_b)
    return user_a, user_b


async def test_review_tables_are_tenant_isolated(
    app_engine: AsyncEngine, review_seeded: tuple[uuid.UUID, uuid.UUID]
) -> None:
    user_a, user_b = review_seeded
    for table in _REVIEW_TABLES:
        assert await _count_table_visible(app_engine, user_a, table) == 1, table
        assert await _count_table_visible(app_engine, user_b, table) == 1, table


async def test_unset_tenant_reads_zero_review_rows(
    app_engine: AsyncEngine, review_seeded: tuple[uuid.UUID, uuid.UUID]
) -> None:
    for table in _REVIEW_TABLES:
        assert await _count_table_visible(app_engine, None, table) == 0, table


async def test_cross_tenant_review_write_rejected(
    app_engine: AsyncEngine, review_seeded: tuple[uuid.UUID, uuid.UUID]
) -> None:
    """A 的租户会话不能写入 user_id=B 的回合（WITH CHECK 谓词拒绝）。"""
    user_a, user_b = review_seeded
    with pytest.raises(ProgrammingError, match="row-level security"):
        async with app_engine.begin() as conn:
            await conn.execute(
                text("select set_config('app.user_id', :uid, true)"), {"uid": str(user_a)}
            )
            await conn.execute(
                text(_INSERT_ROUND_SQL),
                {
                    "id": f"rnd_{uuid.uuid4().hex[:32]}",
                    "uid": user_b,
                    "open_trade_id": f"trd_{uuid.uuid4().hex[:12]}",
                    "close_trade_id": f"trd_{uuid.uuid4().hex[:12]}",
                    "open_date": date(2026, 1, 5),
                    "close_date": date(2026, 1, 7),
                },
            )
