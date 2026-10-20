"""越权负向矩阵（Architecture §14.2 / DEV-PLAN P1-16）。

一期每个业务端点都要在"别人的资源"上返回 404（不泄露存在性），
且 `admin` 角色（运维）不得读写业务明细 → 403 `ADMIN_CANNOT_READ_TRADES`。
未携带凭证 → 401。

与 `backend/tests/api/` 下逐端点的正向/越权用例互补：这里只做**矩阵化**的
横向覆盖，参数化保证"新增端点忘记加归属校验"能被立刻发现。
"""

from __future__ import annotations

import uuid
from datetime import date
from typing import Any

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession
from stock_contracts.errors import ErrorCode
from stock_ledger.models import ImportBatch, Trade
from stock_platform.models import Job, JobEvent

pytestmark = [pytest.mark.isolation, pytest.mark.api]


# ── 直接落库的脚手架（owner 身份，绕过 RLS）──────────────────


async def _seed_trade(engine: AsyncEngine, *, user_id: uuid.UUID) -> str:
    trade_id = f"trd_{uuid.uuid4().hex[:12]}"
    async with AsyncSession(engine) as session:
        session.add(
            Trade(
                id=trade_id,
                user_id=user_id,
                batch_id=None,
                trade_date=date(2026, 6, 15),
                security_code="600519",
                security_name="贵州茅台",
                side="buy",
                quantity_units=1_000_000,
                price_units=1_000_000,
                amount_units=1_000_000,
                commission_units=0,
                stamp_tax_units=0,
                other_fee_units=0,
                cash_balance_units=None,
                cash_balance_available=False,
                occurrence_idx=0,
                source="import",
                version=1,
                dedupe_key=f"600519:{uuid.uuid4().hex[:10]}",
            )
        )
        await session.commit()
    return trade_id


async def _seed_batch(engine: AsyncEngine, *, user_id: uuid.UUID) -> str:
    batch_id = f"bat_{uuid.uuid4().hex[:12]}"
    async with AsyncSession(engine) as session:
        session.add(
            ImportBatch(
                id=batch_id,
                user_id=user_id,
                preview_id=None,
                status="succeeded",
                mode="full",
                broker="huatai",
                file_name="bill.csv",
                file_sha256="0" * 64,
                file_bytes=128,
                parsed_count=1,
                inserted_count=1,
                skipped_count=0,
                quarantined_count=0,
                columns={"成交日期": "trade_date"},
                warnings=[],
                assumptions={},
                ranges={},
            )
        )
        await session.commit()
    return batch_id


async def _seed_job(engine: AsyncEngine, *, user_id: uuid.UUID) -> str:
    job_id = f"job_{uuid.uuid4().hex[:12]}"
    async with AsyncSession(engine) as session:
        session.add(
            Job(
                id=job_id,
                user_id=user_id,
                kind="import_commit",
                scope_key=f"{user_id}:{uuid.uuid4().hex[:6]}",
                dedupe_key=f"import_commit:{uuid.uuid4().hex[:8]}",
                canonical_params="{}",
                params={},
                status="pending",
                priority=100,
                attempt=0,
                max_attempts=3,
                progress=0,
                stage="queued",
            )
        )
        await session.flush()
        session.add(JobEvent(job_id=job_id, seq=1, type="progress", payload={"progress": 5}))
        await session.commit()
    return job_id


# ── 矩阵 ─────────────────────────────────────────────────────

#: 资源级端点：用**别人**的 id 访问必须 404（且不泄露存在性）
RESOURCE_MATRIX: list[tuple[str, str]] = [
    ("get", "/trades/{trade_id}"),
    ("delete", "/trades/{trade_id}"),
    ("get", "/trades/{trade_id}/history"),
    ("post", "/trades/{trade_id}/restore"),
    ("get", "/imports/{batch_id}"),
    ("get", "/imports/{batch_id}/quarantine"),
    ("post", "/imports/{batch_id}/revoke"),
    ("get", "/jobs/{job_id}"),
    ("post", "/jobs/{job_id}/cancel"),
    ("get", "/jobs/{job_id}/events"),
]

#: 业务数据端点：admin 一律 403（运维角色不碰明细）
ADMIN_FORBIDDEN: list[tuple[str, str]] = [
    ("get", "/trades"),
    ("get", "/reconciliation"),
    ("get", "/imports"),
]


async def _call(
    client: httpx.AsyncClient, method: str, url: str, headers: dict[str, str]
) -> httpx.Response:
    return await client.request(method.upper(), url, headers=headers)


@pytest.mark.parametrize(("method", "template"), RESOURCE_MATRIX)
async def test_cross_account_returns_404(
    client: httpx.AsyncClient,
    api_prefix: str,
    account_factory: Any,
    owner_engine: AsyncEngine,
    method: str,
    template: str,
) -> None:
    owner = await account_factory()
    intruder = await account_factory()
    url = api_prefix + template.format(
        trade_id=await _seed_trade(owner_engine, user_id=owner.user_id),
        batch_id=await _seed_batch(owner_engine, user_id=owner.user_id),
        job_id=await _seed_job(owner_engine, user_id=owner.user_id),
    )
    resp = await _call(client, method, url, intruder.headers)
    assert resp.status_code == 404, f"{method.upper()} {template} → {resp.status_code}"
    assert resp.json()["code"] == ErrorCode.NOT_FOUND.value


@pytest.mark.parametrize(("method", "template"), ADMIN_FORBIDDEN)
async def test_admin_forbidden_on_business_data(
    client: httpx.AsyncClient,
    api_prefix: str,
    admin: Any,
    method: str,
    template: str,
) -> None:
    resp = await _call(client, method, api_prefix + template, admin.headers)
    assert resp.status_code == 403, f"{method.upper()} {template} → {resp.status_code}"
    assert resp.json()["code"] == ErrorCode.ADMIN_CANNOT_READ_TRADES.value


async def test_unauthenticated_returns_401(client: httpx.AsyncClient, api_prefix: str) -> None:
    """无凭证 → 401；实现按契约用 TOKEN_EXPIRED 兜"缺少认证凭证"（错误码表 §3 无 UNAUTHENTICATED）。"""
    resp = await client.get(f"{api_prefix}/trades")
    assert resp.status_code == 401
    assert resp.json()["code"] == ErrorCode.TOKEN_EXPIRED.value
