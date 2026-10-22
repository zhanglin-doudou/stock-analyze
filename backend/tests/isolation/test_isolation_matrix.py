"""越权负向矩阵（Architecture §14.2 / DEV-PLAN P1-16）。

一期每个业务端点都要在"别人的资源"上返回 404（不泄露存在性），
且 `admin` 角色（运维）不得读写业务明细 → 403 `ADMIN_CANNOT_READ_TRADES`。
未携带凭证 → 401。

与 `backend/tests/api/` 下逐端点的正向/越权用例互补：这里只做**矩阵化**的
横向覆盖，参数化保证"新增端点忘记加归属校验"能被立刻发现。
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from typing import Any

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession
from stock_contracts.errors import ErrorCode
from stock_ledger.models import ImportBatch, Trade
from stock_platform.models import ApiToken, Job, JobEvent

pytestmark = [pytest.mark.isolation, pytest.mark.api]


# ── 直接落库的脚手架（owner 身份，绕过 RLS）──────────────────


async def _seed_trade(
    engine: AsyncEngine,
    *,
    user_id: uuid.UUID,
    amount_units: int = 1_000_000,
    cash_balance_units: int | None = None,
) -> str:
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
                amount_units=amount_units,
                commission_units=0,
                stamp_tax_units=0,
                other_fee_units=0,
                cash_balance_units=cash_balance_units,
                cash_balance_available=cash_balance_units is not None,
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


async def _seed_token(engine: AsyncEngine, *, user_id: uuid.UUID) -> str:
    """直插一条 PAT 元数据（明文不可恢复）；用于 DELETE /tokens/{token_id} 的跨租户用例。"""
    token_id = f"tok_{uuid.uuid4().hex[:12]}"
    async with AsyncSession(engine) as session:
        session.add(
            ApiToken(
                id=token_id,
                user_id=user_id,
                name="seeded",
                token_prefix=f"pat_{uuid.uuid4().hex[:8]}",
                token_hash=f"seed-{uuid.uuid4().hex}",
                scopes=["read"],
                expires_at=datetime(2027, 1, 1, tzinfo=UTC),
            )
        )
        await session.commit()
    return token_id


# ── 矩阵 ─────────────────────────────────────────────────────

#: 资源级端点：用**别人**的 id 访问必须 404（且不泄露存在性）
RESOURCE_MATRIX: list[tuple[str, str]] = [
    ("get", "/trades/{trade_id}"),
    ("patch", "/trades/{trade_id}"),
    ("delete", "/trades/{trade_id}"),
    ("get", "/trades/{trade_id}/history"),
    ("post", "/trades/{trade_id}/restore"),
    ("delete", "/tokens/{token_id}"),
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

#: 列表型端点：A/B 各自造数据，B 的列表不得包含 A 的行（跨租户列表隔离）
LIST_ISOLATION_MATRIX: list[tuple[str, str]] = [
    ("get", "/trades"),
    ("get", "/imports"),
    ("get", "/jobs"),
    ("get", "/reconciliation"),
]

#: 无凭证 → 401：覆盖全部受保护端点（gate_tenant_coverage.py 校验 23/23 完整）。
#: 少数端点 body/header 校验先于认证依赖执行，测试侧用 `_unauth_kwargs` 补最小合法载荷，
#: 保证断言的是「认证层」而不是 422。
UNAUTHENTICATED_MATRIX: list[tuple[str, str]] = [
    ("post", "/auth/logout"),
    ("get", "/auth/me"),
    ("delete", "/account"),
    ("post", "/tokens"),
    ("get", "/tokens"),
    ("delete", "/tokens/{token_id}"),
    ("get", "/jobs"),
    ("get", "/jobs/{job_id}"),
    ("get", "/jobs/{job_id}/events"),
    ("post", "/jobs/{job_id}/cancel"),
    ("get", "/trades"),
    ("get", "/trades/{trade_id}"),
    ("patch", "/trades/{trade_id}"),
    ("delete", "/trades/{trade_id}"),
    ("post", "/trades/{trade_id}/restore"),
    ("get", "/trades/{trade_id}/history"),
    ("get", "/reconciliation"),
    ("post", "/imports/preview"),
    ("post", "/imports"),
    ("get", "/imports"),
    ("get", "/imports/{batch_id}"),
    ("get", "/imports/{batch_id}/quarantine"),
    ("post", "/imports/{batch_id}/revoke"),
]

#: 路径参数占位值（只用于越权/401 用例；资源不存在时恰好也走 404 分支）。
_PLACEHOLDERS = {
    "trade_id": "trd_01J9Z6PLACEHOLDER",
    "batch_id": "bat_01J9Z6PLACEHOLDER",
    "job_id": "job_01J9Z6PLACEHOLDER",
    "token_id": "tok_01J9Z6PLACEHOLDER",
}


def _fill(template: str, **ids: str) -> str:
    return template.format(**{**_PLACEHOLDERS, **ids})


def _unauth_kwargs(method: str, template: str) -> dict[str, Any]:
    """让请求先通过 body/header 校验，从而抵达认证层（否则会先 422）。"""
    kwargs: dict[str, Any] = {}
    if template == "/account":
        kwargs["headers"] = {"Idempotency-Key": "unauth-probe-" + "x" * 20}
    elif template == "/tokens" and method == "post":
        kwargs["json"] = {"name": "probe", "scopes": ["read"]}
    elif template == "/trades/{trade_id}" and method == "patch":
        kwargs["headers"] = {"If-Match": '"1"'}
        kwargs["json"] = {"note": "probe"}
    elif template == "/imports/preview":
        kwargs["files"] = {"file": ("probe.csv", b"date,code\n", "text/csv")}
    elif template == "/imports" and method == "post":
        kwargs["json"] = {"preview_id": "prv_probe", "mode": "full"}
    return kwargs


async def _call(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    headers: dict[str, str],
    *,
    json_body: dict[str, Any] | None = None,
) -> httpx.Response:
    return await client.request(method.upper(), url, headers=headers, json=json_body)


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
    ids = {
        "trade_id": await _seed_trade(owner_engine, user_id=owner.user_id),
        "batch_id": await _seed_batch(owner_engine, user_id=owner.user_id),
        "job_id": await _seed_job(owner_engine, user_id=owner.user_id),
        "token_id": await _seed_token(owner_engine, user_id=owner.user_id),
    }
    headers = dict(intruder.headers)
    json_body: dict[str, Any] | None = None
    if method == "patch":
        headers["If-Match"] = '"1"'
        json_body = {"note": "跨租户越权尝试"}
    # intruder 用「真实存在但属于别人」的 id 访问
    url = api_prefix + _fill(template, **ids)
    resp = await _call(client, method, url, headers, json_body=json_body)
    assert resp.status_code == 404, f"{method.upper()} {template} → {resp.status_code}"
    assert resp.json()["code"] == ErrorCode.NOT_FOUND.value


@pytest.mark.parametrize(("method", "template"), LIST_ISOLATION_MATRIX)
async def test_cross_account_list_isolated(
    client: httpx.AsyncClient,
    api_prefix: str,
    account_factory: Any,
    owner_engine: AsyncEngine,
    method: str,
    template: str,
) -> None:
    owner = await account_factory()
    intruder = await account_factory()
    # owner 造数据（两条带余额流水制造一条对账断链；batch/job 各一条）
    await _seed_trade(
        owner_engine, user_id=owner.user_id, amount_units=1_000_000, cash_balance_units=50_000_000
    )
    trade_id = await _seed_trade(
        owner_engine, user_id=owner.user_id, amount_units=1_000_000, cash_balance_units=99_000_000
    )
    batch_id = await _seed_batch(owner_engine, user_id=owner.user_id)
    job_id = await _seed_job(owner_engine, user_id=owner.user_id)

    url = api_prefix + template
    if template == "/reconciliation":
        url += "?period=2026-06"
    resp = await _call(client, method, url, intruder.headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    if template == "/reconciliation":
        assert body["breaks"] == [], "B 的对账看到了 A 的断链"
        assert body["checked_rows"] == 0
        owner_resp = await _call(client, method, url, owner.headers)
        assert owner_resp.status_code == 200
        assert owner_resp.json()["break_count"] == 1, "A 自己的断链应可见（防止假阳性）"
        return

    id_field = {"/trades": "trade_id", "/imports": "batch_id", "/jobs": "job_id"}[template]
    expected = {"trade_id": trade_id, "batch_id": batch_id, "job_id": job_id}[id_field]
    assert body["items"] == [], f"B 的 {template} 泄露了 A 的数据"
    owner_resp = await _call(client, method, url, owner.headers)
    assert owner_resp.status_code == 200
    owner_ids = {item[id_field] for item in owner_resp.json()["items"]}
    assert expected in owner_ids, "A 自己的数据应可见（防止假阳性）"


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


@pytest.mark.parametrize(("method", "template"), UNAUTHENTICATED_MATRIX)
async def test_unauthenticated_returns_401(
    client: httpx.AsyncClient,
    api_prefix: str,
    method: str,
    template: str,
) -> None:
    """无凭证 → 401；全部受保护端点参数化（实现按契约用 TOKEN_EXPIRED 兜"缺少认证凭证"）。"""
    resp = await client.request(
        method.upper(), api_prefix + _fill(template), **_unauth_kwargs(method, template)
    )
    assert resp.status_code == 401, f"{method.upper()} {template} → {resp.status_code} {resp.text}"
    assert resp.json()["code"] == ErrorCode.TOKEN_EXPIRED.value
