"""G7：platform 租户表 RLS 直连验证（Testing §4.3 八类场景）。

覆盖 `platform.job / idempotency_key / user_setting / outbox_event` 四张 G7 新启用
RLS 的表，并回归 `ledger` 的 admin 不可读明细约束：

  R1 A/B 租户隔离（SELECT 只看到自己的行）
  R2 未设 `app.user_id` → 0 行（fail-closed）
  R3 显式 `WHERE user_id=<他人>` 仍 0 行（RLS 先于谓词）
  R4 跨租户 UPDATE/DELETE 影响 0 行
  R5 INSERT 伪造成他人 → WITH CHECK 拒绝（InsufficientPrivilege）
  R6 每张租户表都有 RLS + 引用 app.user_id 的 tenant 策略（review 0 表时跳过）
  R7 连接池归还后再取：`app.user_id` 已随事务失效（无串号）
  R8 admin 直连交易明细 0 行（job 亦然）

全部用 **原始 SQL + asyncpg 引擎**（绕过 SQLAlchemy ORM / 仓储），否则只是把应用层
测试再跑一遍。库由 `STOCK_DB_NAME=stock_g7_check` 等环境变量指定。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date

import pytest
from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from stock_platform.config import Settings

pytestmark = [pytest.mark.integration]

TENANT_PREDICATE_FRAGMENT = "app.user_id"

# 认证前置 / 系统表：按设计不启用 RLS（与 0002 迁移 docstring、gate_rls_coverage.py 一致）
EXEMPT_TABLES: frozenset[tuple[str, str]] = frozenset(
    {
        ("platform", "user"),
        ("platform", "refresh_token"),
        ("platform", "api_token"),
        ("platform", "login_attempt"),
        ("platform", "rate_limit_bucket"),
        ("platform", "config_release"),
        ("platform", "schema_version"),
        ("platform", "backup_run"),
        ("platform", "audit_log"),
        ("platform", "job_event"),
    }
)

PLATFORM_RLS_TABLES = ("job", "idempotency_key", "user_setting", "outbox_event")
LEDGER_RLS_TABLES = (
    "trade",
    "trade_history",
    "import_batch",
    "import_preview",
    "quarantine_row",
    "summary_rule",
    "reconciliation",
)


@dataclass(frozen=True, slots=True)
class TenantTable:
    """一张 G7 租户表的插入/变更模板（必填列按 0001 迁移 NOT NULL 约束）。"""

    name: str
    insert_sql: str  # 使用 :uid（user_id）与可选 :key
    uses_key: bool = True


_TABLES: tuple[TenantTable, ...] = (
    TenantTable(
        name="job",
        insert_sql=(
            "insert into platform.job (id, user_id, kind, scope_key, dedupe_key, "
            "canonical_params, params, status, priority, attempt, max_attempts, "
            "cancel_requested, progress, stage) "
            "values (:key, :uid, :key, :key, :key, '{}', '{}', 'pending', 100, 0, 3, "
            "false, 0, 'queued')"
        ),
    ),
    TenantTable(
        name="idempotency_key",
        insert_sql=(
            "insert into platform.idempotency_key "
            "(user_id, endpoint, key, request_hash, expires_at) "
            "values (:uid, 'ep', :key, 'h', now() + interval '1 day')"
        ),
    ),
    TenantTable(
        name="user_setting",
        insert_sql=(
            "insert into platform.user_setting "
            "(user_id, benchmark_id, privacy_flags, display_prefs, version) "
            "values (:uid, 'hs300_tr', '{}', '{}', 1)"
        ),
        uses_key=False,
    ),
    TenantTable(
        name="outbox_event",
        insert_sql=(
            "insert into platform.outbox_event (type, aggregate, user_id, payload, attempts) "
            "values ('t', 'agg', :uid, '{}', 0)"
        ),
        uses_key=False,
    ),
)
TABLE_NAMES = tuple(table.name for table in _TABLES)


async def _insert_user(engine: AsyncEngine, *, role: str = "user") -> uuid.UUID:
    user_id = uuid.uuid4()
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "insert into platform.user (id, email, password_hash, role, status) "
                "values (:id, :email, 'hash', :role, 'active')"
            ),
            {"id": user_id, "email": f"g7-{user_id.hex[:12]}@example.test", "role": role},
        )
    return user_id


def _params(user_id: uuid.UUID, key: str, *, uses_key: bool) -> dict[str, object]:
    return {"uid": user_id, "key": key} if uses_key else {"uid": user_id}


async def _insert_row(
    engine: AsyncEngine,
    table: TenantTable,
    user_id: uuid.UUID,
    *,
    key: str,
    tenant: uuid.UUID | None = None,
) -> None:
    """插入一行；`tenant` 给出时先 SET LOCAL（用于 app 角色验证 WITH CHECK 放行）。"""
    async with engine.begin() as conn:
        if tenant is not None:
            await conn.execute(
                text("select set_config('app.user_id', :uid, true)"), {"uid": str(tenant)}
            )
        await conn.execute(text(table.insert_sql), _params(user_id, key, uses_key=table.uses_key))


async def _app_scalar(
    engine: AsyncEngine,
    tenant: uuid.UUID | None,
    sql: str,
    params: dict[str, object] | None = None,
) -> object:
    """以 stock_app 角色执行 SELECT；tenant=None 表示不设 app.user_id。"""
    async with engine.begin() as conn:
        if tenant is not None:
            await conn.execute(
                text("select set_config('app.user_id', :uid, true)"), {"uid": str(tenant)}
            )
        return await conn.scalar(text(sql), params or {})


async def _app_execute(
    engine: AsyncEngine, tenant: uuid.UUID, sql: str, params: dict[str, object]
) -> int:
    """以 stock_app 角色执行 DML，返回影响行数。"""
    async with engine.begin() as conn:
        await conn.execute(
            text("select set_config('app.user_id', :uid, true)"), {"uid": str(tenant)}
        )
        result = await conn.execute(text(sql), params)
        return int(result.rowcount or 0)


async def _seed_trade(owner_engine: AsyncEngine, user_id: uuid.UUID) -> str:
    trade_id = f"trd_{uuid.uuid4().hex[:12]}"
    async with owner_engine.begin() as conn:
        await conn.execute(
            text(
                "insert into ledger.trade (id, user_id, trade_date, security_code, "
                "security_name, side, quantity_units, price_units, amount_units, "
                "commission_units, stamp_tax_units, other_fee_units, "
                "cash_balance_available, occurrence_idx, source, version, dedupe_key) "
                "values (:id, :uid, :d, '600519', '贵州茅台', 'buy', 1000000, 1000000, "
                "-1000000, 0, 0, 0, false, 0, 'import', 1, :dk)"
            ),
            {
                "id": trade_id,
                "uid": user_id,
                "d": date(2026, 6, 30),
                "dk": f"g7:{uuid.uuid4().hex[:10]}",
            },
        )
    return trade_id


@pytest.fixture
async def two_tenants(owner_engine: AsyncEngine) -> tuple[uuid.UUID, uuid.UUID]:
    return await _insert_user(owner_engine), await _insert_user(owner_engine)


# ── R1/R2/R3：读隔离与 fail-closed ─────────────────────────────


@pytest.mark.parametrize("table_name", TABLE_NAMES)
async def test_tenant_isolation_and_fail_closed(
    owner_engine: AsyncEngine,
    app_engine: AsyncEngine,
    two_tenants: tuple[uuid.UUID, uuid.UUID],
    table_name: str,
) -> None:
    user_a, user_b = two_tenants
    table = next(item for item in _TABLES if item.name == table_name)
    await _insert_row(owner_engine, table, user_a, key=f"a-{uuid.uuid4().hex[:8]}")
    await _insert_row(owner_engine, table, user_b, key=f"b-{uuid.uuid4().hex[:8]}")

    count_sql = f"select count(*) from platform.{table_name}"
    # R1：A/B 各自只看到 1 行，且行归属正确
    assert (
        await _app_scalar(
            app_engine,
            user_a,
            f"select count(*) from platform.{table_name} where user_id = :uid",
            {"uid": user_a},
        )
        == 1
    )
    assert await _app_scalar(app_engine, user_b, count_sql) == 1
    assert await _app_scalar(app_engine, user_a, count_sql) == 1
    # R2：未设 app.user_id → 0 行（不是全表）
    assert await _app_scalar(app_engine, None, count_sql) == 0
    # R3：显式 WHERE 他人 user_id 仍 0 行
    assert (
        await _app_scalar(
            app_engine,
            user_a,
            f"select count(*) from platform.{table_name} where user_id = :uid",
            {"uid": user_b},
        )
        == 0
    )


# ── R4：跨租户 UPDATE/DELETE 影响 0 行 ─────────────────────────


@pytest.mark.parametrize("table_name", TABLE_NAMES)
async def test_cross_tenant_update_delete_affect_zero(
    owner_engine: AsyncEngine,
    app_engine: AsyncEngine,
    two_tenants: tuple[uuid.UUID, uuid.UUID],
    table_name: str,
) -> None:
    user_a, user_b = two_tenants
    table = next(item for item in _TABLES if item.name == table_name)
    await _insert_row(owner_engine, table, user_a, key=f"a-{uuid.uuid4().hex[:8]}")
    await _insert_row(owner_engine, table, user_b, key=f"b-{uuid.uuid4().hex[:8]}")

    updated = await _app_execute(
        app_engine,
        user_a,
        f"update platform.{table_name} set user_id = user_id where user_id = :uid",
        {"uid": user_b},
    )
    deleted = await _app_execute(
        app_engine,
        user_a,
        f"delete from platform.{table_name} where user_id = :uid",
        {"uid": user_b},
    )
    assert (updated, deleted) == (0, 0)
    # B 的行仍在（owner 视角）
    assert await _app_scalar(owner_engine, None, f"select count(*) from platform.{table_name}") == 2


# ── R5：INSERT 伪造成他人 → WITH CHECK 拒绝 ────────────────────


@pytest.mark.parametrize("table_name", TABLE_NAMES)
async def test_insert_foreign_user_rejected_by_with_check(
    owner_engine: AsyncEngine,
    app_engine: AsyncEngine,
    two_tenants: tuple[uuid.UUID, uuid.UUID],
    table_name: str,
) -> None:
    user_a, user_b = two_tenants
    table = next(item for item in _TABLES if item.name == table_name)
    with pytest.raises(ProgrammingError):
        async with app_engine.begin() as conn:
            await conn.execute(
                text("select set_config('app.user_id', :uid, true)"), {"uid": str(user_a)}
            )
            await conn.execute(
                text(table.insert_sql),
                _params(user_b, f"x-{uuid.uuid4().hex[:8]}", uses_key=table.uses_key),
            )
    # 副作用未发生（owner 视角 0 行）
    assert await _app_scalar(owner_engine, None, f"select count(*) from platform.{table_name}") == 0


@pytest.mark.parametrize("table_name", TABLE_NAMES)
async def test_insert_own_row_allowed(
    owner_engine: AsyncEngine,
    app_engine: AsyncEngine,
    two_tenants: tuple[uuid.UUID, uuid.UUID],
    table_name: str,
) -> None:
    """WITH CHECK 不能误杀合法写入（API/worker 的正常路径）。"""
    user_a, _ = two_tenants
    table = next(item for item in _TABLES if item.name == table_name)
    await _insert_row(app_engine, table, user_a, key=f"a-{uuid.uuid4().hex[:8]}", tenant=user_a)
    assert await _app_scalar(owner_engine, None, f"select count(*) from platform.{table_name}") == 1


# ── R6：每张租户表都有 RLS + tenant 策略（review 0 表时跳过） ──


async def test_every_tenant_table_has_tenant_policy(app_engine: AsyncEngine) -> None:
    async with app_engine.begin() as conn:
        columns = {
            (row[0], row[1])
            for row in await conn.execute(
                text(
                    # 用 pg_catalog 而非 information_schema.columns：后者只显示当前角色
                    # 有权限的列，P2-02 若忘 GRANT 会“看不见”而漏检。
                    "select n.nspname, c.relname from pg_class c "
                    "join pg_namespace n on n.oid = c.relnamespace "
                    "join pg_attribute a on a.attrelid = c.oid "
                    "and a.attname = 'user_id' and not a.attisdropped "
                    "where n.nspname in ('platform','ledger','review') "
                    "and c.relkind in ('r','p')"
                )
            )
        }
        expected = set(columns) - EXEMPT_TABLES
        assert expected, "未枚举到任何租户表：迁移未执行？"
        for schema, table in sorted(expected):
            rls = await conn.scalar(
                text(
                    "select relrowsecurity from pg_class c "
                    "join pg_namespace n on n.oid = c.relnamespace "
                    "where n.nspname = :schema and c.relname = :table"
                ),
                {"schema": schema, "table": table},
            )
            assert rls is True, f"{schema}.{table} 未启用 RLS"
            policy_count = await conn.scalar(
                text(
                    "select count(*) from pg_policies where schemaname = :schema "
                    "and tablename = :table and qual like :needle "
                    "and with_check like :needle"
                ),
                {"schema": schema, "table": table, "needle": f"%{TENANT_PREDICATE_FRAGMENT}%"},
            )
            assert int(policy_count or 0) >= 1, f"{schema}.{table} 缺少 tenant 策略"

        # 本任务显式要求的 platform 4 表 + ledger 7 表都在集合内
        assert {("platform", name) for name in PLATFORM_RLS_TABLES} <= expected
        assert {("ledger", name) for name in LEDGER_RLS_TABLES} <= expected
        # review 当前 0 表：集合为空也通过；P2-02 建表后自动进入 expected 并在上方循环强校验。
        if not any(schema == "review" for schema, _table in expected):
            review_count = await conn.scalar(
                text(
                    "select count(*) from pg_class c "
                    "join pg_namespace n on n.oid = c.relnamespace "
                    "where n.nspname = 'review' and c.relkind in ('r','p')"
                )
            )
            assert int(review_count or 0) == 0, "review 已建表但未带 user_id，无法被 RLS 覆盖检查"


# ── R7：连接池串号（SET LOCAL 随事务结束失效）─────────────────


async def test_pool_checkout_has_no_leftover_tenant(settings: Settings) -> None:
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
        async with engine.begin() as conn:
            leftover = await conn.scalar(text("select current_setting('app.user_id', true)"))
        assert leftover in (None, "")
    finally:
        await engine.dispose()


# ── R8：app 不能 DDL；admin 直连业务明细 0 行 ──────────────────


async def test_app_role_cannot_execute_ddl(app_engine: AsyncEngine) -> None:
    with pytest.raises(ProgrammingError):
        async with app_engine.begin() as conn:
            await conn.execute(text("create table platform.ddl_probe_g7 (id int)"))


async def test_admin_direct_connection_cannot_read_trades(
    owner_engine: AsyncEngine, app_engine: AsyncEngine, two_tenants: tuple[uuid.UUID, uuid.UUID]
) -> None:
    user_a, _ = two_tenants
    admin_id = await _insert_user(owner_engine, role="admin")
    await _seed_trade(owner_engine, user_a)

    assert await _app_scalar(app_engine, None, "select count(*) from ledger.trade") == 0
    assert await _app_scalar(app_engine, admin_id, "select count(*) from ledger.trade") == 0
    # admin 自己的租户行可见性同样只按 user_id 过滤（没有全局读能力）
    assert await _app_scalar(app_engine, admin_id, "select count(*) from platform.job") == 0
