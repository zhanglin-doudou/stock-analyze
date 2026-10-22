"""公共夹具：真实 PostgreSQL + 无网络端口的 ASGI 客户端。

硬约束：不 mock 数据库（RLS 必须真连库）；每个用例自造数据自清理，不依赖执行顺序。
契约角色分工：
  * ``stock_owner``（owner DSN）：DDL/TRUNCATE/系统行写入，绕过 RLS；
  * ``stock_app``（app DSN，非 owner、NOBYPASSRLS）：应用运行时，受 RLS 约束。
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from stock_api.app import create_app
from stock_api.version import API_PREFIX
from stock_platform.config import Settings, get_settings
from stock_platform.security import hasher_for

# ── 常量 ────────────────────────────────────────────────────

DEFAULT_PASSWORD = "Str0ng-Passw0rd!"

# 需要清空的业务表（不含 platform.schema_version，避免破坏“启动即校验”）。
_PLATFORM_TABLES = (
    "user",
    "refresh_token",
    "api_token",
    "job",
    "job_event",
    "idempotency_key",
    "outbox_event",
    "audit_log",
    "user_setting",
    "config_release",
    "rate_limit_bucket",
    "login_attempt",
    "backup_run",
)
_LEDGER_TABLES = (
    "import_batch",
    "import_preview",
    "trade",
    "trade_history",
    "quarantine_row",
    "summary_rule",
    "reconciliation",
)
# review 表由迁移 0003 建立（Period2-02）；三表都带 RLS，按 user 级联清理。
_REVIEW_TABLES = (
    "round",
    "round_fill",
    "round_build_state",
)

_TRUNCATE_SQL = (
    "TRUNCATE TABLE "
    + ", ".join(f'platform."{name}"' for name in _PLATFORM_TABLES)
    + ", "
    + ", ".join(f'ledger."{name}"' for name in _LEDGER_TABLES)
    + ", "
    + ", ".join(f'review."{name}"' for name in _REVIEW_TABLES)
    + " RESTART IDENTITY CASCADE"
)


# ── 基础设施夹具 ────────────────────────────────────────────


@pytest.fixture(scope="session")
def settings() -> Settings:
    """读取根 .env 的真实配置（库名/角色/密码）。"""
    return get_settings()


@pytest_asyncio.fixture
async def owner_engine(settings: Settings) -> AsyncIterator[AsyncEngine]:
    """owner 引擎：脚手架（造用户 / TRUNCATE / 系统行）。

    与默认的函数级事件循环同域，避免跨 loop 复用 asyncpg 连接。
    """
    engine = create_async_engine(settings.owner_dsn(), pool_pre_ping=True)
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def app_engine(settings: Settings) -> AsyncIterator[AsyncEngine]:
    """应用角色引擎：用于 RLS 双层隔离直连验证（非 owner、NOBYPASSRLS）。"""
    engine = create_async_engine(settings.app_dsn(), pool_pre_ping=True)
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest_asyncio.fixture(autouse=True)
async def _clean_db(owner_engine: AsyncEngine) -> AsyncIterator[None]:
    """每个用例前清空业务表并重置自增；不重跑迁移。"""
    async with owner_engine.begin() as conn:
        await conn.execute(text(_TRUNCATE_SQL))
    yield


@pytest.fixture
def app(settings: Settings) -> Any:
    """真实 FastAPI 应用（不跑 lifespan：schema 校验已由迁移保证）。"""
    return create_app(settings)


@pytest_asyncio.fixture
async def client(app: Any) -> AsyncIterator[httpx.AsyncClient]:
    """进程内 ASGI 客户端（ASGITransport，不起网络端口）。"""
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver", follow_redirects=False
    ) as ac:
        yield ac


# ── 账号 / 登录辅助 ─────────────────────────────────────────


@dataclass(slots=True)
class TestAccount:
    """测试账号：已注册、已登录，携带可直接使用的 Authorization 头。"""

    user_id: uuid.UUID
    email: str
    password: str
    access_token: str
    refresh_token: str
    role: str
    headers: dict[str, str] = field(default_factory=dict)


async def _insert_user(
    engine: AsyncEngine, *, email: str, password: str, role: str = "user"
) -> uuid.UUID:
    """以 owner 身份直插用户行（绕过注册接口，便于 admin/前置场景）。"""
    user_id = uuid.uuid4()
    password_hash = hasher_for(1).hash(password)
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "insert into platform.user (id, email, password_hash, role, status) "
                "values (:id, :email, :hash, :role, 'active')"
            ),
            {"id": user_id, "email": email, "hash": password_hash, "role": role},
        )
    return user_id


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest_asyncio.fixture
async def account_factory(
    client: httpx.AsyncClient, owner_engine: AsyncEngine
) -> Callable[..., Awaitable[TestAccount]]:
    """造账号并登录，返回可用凭证。

    用法::

        acc = await account_factory(role="admin")
        r = await client.get(f"{API_PREFIX}/trades", headers=acc.headers)
    """

    async def _make(
        *,
        email: str | None = None,
        password: str = DEFAULT_PASSWORD,
        role: str = "user",
    ) -> TestAccount:
        resolved_email = email or f"user-{uuid.uuid4().hex[:12]}@example.com"
        user_id = await _insert_user(
            owner_engine, email=resolved_email, password=password, role=role
        )
        resp = await client.post(
            f"{API_PREFIX}/auth/login",
            json={"email": resolved_email, "password": password},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        return TestAccount(
            user_id=user_id,
            email=resolved_email,
            password=password,
            access_token=body["access_token"],
            refresh_token=body["refresh_token"],
            role=role,
            headers=_auth(body["access_token"]),
        )

    return _make


@pytest_asyncio.fixture
async def user(account_factory: Callable[..., Awaitable[TestAccount]]) -> TestAccount:
    """默认普通用户账号。"""
    return await account_factory()


@pytest_asyncio.fixture
async def admin(account_factory: Callable[..., Awaitable[TestAccount]]) -> TestAccount:
    """默认管理员账号。"""
    return await account_factory(role="admin")


@pytest.fixture
def api_prefix() -> str:
    return API_PREFIX
