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
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession
from stock_contracts.errors import ErrorCode
from stock_ledger.models import ImportBatch, SummaryRule, Trade
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


async def _seed_rule(engine: AsyncEngine, *, user_id: uuid.UUID) -> str:
    """直插一条 owner 的用户规则；用于 PATCH/DELETE /summary-rules/{rule_id} 跨租户用例。"""
    rule_id = f"rul_{uuid.uuid4().hex[:12]}"
    async with AsyncSession(engine) as session:
        session.add(
            SummaryRule(
                id=rule_id,
                user_id=user_id,
                code=f"usr_{uuid.uuid4().hex[:8]}",
                name="越权探针规则",
                category="other",
                side="both",
                match_type="exact",
                pattern=f"probe-{uuid.uuid4().hex[:8]}",
                priority=100,
                enabled=True,
                is_system=False,
                version=1,
            )
        )
        await session.commit()
    return rule_id


async def _seed_round(engine: AsyncEngine, *, user_id: uuid.UUID) -> str:
    """直插一条 owner 的回合（owner 绕过 RLS；走 raw SQL 以避免测试耦合 review 包内部）。"""
    round_id = f"rnd_{uuid.uuid4().hex[:12]}"
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "insert into review.round (id, user_id, security_code, security_name, status, "
                "open_trade_id, close_trade_id, open_date, close_date, quantity_units, cost_units, "
                "proceeds_units, dividend_units, pnl_units, return_rate_units, holding_days, "
                "data_error, oversell_units, fills_count, rebuilt_at) "
                "values (:id, :user_id, '600519', '贵州茅台', 'closed', "
                "'trd_probe_buy', 'trd_probe_sell', :d, :d, 1000000, 1000000, 1200000, 0, "
                "200000, 20000000, 5, false, 0, 0, now())"
            ),
            {"id": round_id, "user_id": user_id, "d": date(2026, 6, 1)},
        )
    return round_id


async def _seed_asset_row(engine: AsyncEngine, *, user_id: uuid.UUID) -> date:
    """直插一条 owner 的资产快照行（owner 绕过 RLS；raw SQL 避免耦合 review 包内部）。"""
    trade_date = date(2026, 6, 30)
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "insert into review.asset_daily (id, user_id, trade_date, cash_units, "
                "market_value_units, total_assets_units, holding_cost_units, "
                "unrealized_pnl_units, holding_count, cashflow_units, "
                "prev_total_assets_units, daily_pnl_units, daily_return_units, status, "
                "status_reason, definition_version, rebuilt_at) "
                "values (:id, :user_id, :d, 1000000, 0, 1000000, 0, 0, 0, 0, "
                "NULL, NULL, NULL, 'ok', NULL, '1.0', now())"
            ),
            {"id": f"ast_{uuid.uuid4().hex}", "user_id": user_id, "d": trade_date},
        )
    return trade_date


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
    # Period2-02（API-016/024/025/030/031）
    ("post", "/imports/{batch_id}/reclassify"),
    ("patch", "/summary-rules/{rule_id}"),
    ("delete", "/summary-rules/{rule_id}"),
    ("get", "/rounds/{round_id}"),
    ("get", "/rounds/{round_id}/fills"),
]

#: 业务数据端点：admin 一律 403（运维角色不碰明细）
ADMIN_FORBIDDEN: list[tuple[str, str]] = [
    ("get", "/trades"),
    ("get", "/reconciliation"),
    ("get", "/imports"),
    # Period2-02：admin 不可读回合/桥接业务数据（身份越权 → 403，非 404）
    ("get", "/rounds"),
    ("get", "/rounds/{round_id}"),
    ("get", "/rounds/{round_id}/fills"),
    ("get", "/reconciliation/bridge"),
    # Period2-03：admin 不可读 NAV + 持仓
    ("get", "/nav"),
    # Period2-16：admin 不可读写资产表（身份越权 → 403，不是 404）
    ("get", "/assets"),
    ("post", "/assets/build"),
    # Period2-06：admin 不可读指标（身份越权 → 403）
    ("get", "/metrics"),
    ("get", "/metrics/{metric_id}"),
    ("get", "/metrics/{metric_id}/explain"),
    ("get", "/metrics/{metric_id}/drilldown"),
    # Period2-12：Dashboard 聚合（身份越权 → 403）
    ("get", "/dashboard"),
]

#: 列表型端点：A/B 各自造数据，B 的列表不得包含 A 的行（跨租户列表隔离）
LIST_ISOLATION_MATRIX: list[tuple[str, str]] = [
    ("get", "/trades"),
    ("get", "/imports"),
    ("get", "/jobs"),
    ("get", "/reconciliation"),
    # Period2-02
    ("get", "/rounds"),
    ("get", "/summary-rules"),
    ("get", "/reconciliation/bridge"),
    # Period2-16：资产表列表（seed 自定义 asset_daily 行，按 date 断言）
    ("get", "/assets"),
    # Period2-12：Dashboard 跨租户不串号（B 的持仓/计数为空，A 自己可见）
    ("get", "/dashboard"),
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
    # Period2-02（API-016/022~025/027~031）
    ("post", "/imports/{batch_id}/reclassify"),
    ("get", "/summary-rules"),
    ("post", "/summary-rules"),
    ("patch", "/summary-rules/{rule_id}"),
    ("delete", "/summary-rules/{rule_id}"),
    ("get", "/reconciliation/bridge"),
    ("post", "/jobs/rebuild-rounds"),
    ("get", "/rounds"),
    ("get", "/rounds/{round_id}"),
    ("get", "/rounds/{round_id}/fills"),
    # Period2-03（API-032 净值与收益 + 每日持仓）
    ("get", "/nav"),
    # Period2-16（API-081/082 资产表）
    ("get", "/assets"),
    ("post", "/assets/build"),
    # Period2-06（API-033/034/035/079 指标）
    ("get", "/metrics"),
    ("get", "/metrics/{metric_id}"),
    ("get", "/metrics/{metric_id}/explain"),
    ("get", "/metrics/{metric_id}/drilldown"),
    # Period2-07（API-083 按月编排；admin 403 由 tests/api/test_api_083 显式覆盖）
    ("post", "/jobs/rebuild-period"),
    # Period2-12（API-084 Dashboard 聚合）
    ("get", "/dashboard"),
]

#: 路径参数占位值（只用于越权/401 用例；资源不存在时恰好也走 404 分支）。
_PLACEHOLDERS = {
    "trade_id": "trd_01J9Z6PLACEHOLDER",
    "batch_id": "bat_01J9Z6PLACEHOLDER",
    "job_id": "job_01J9Z6PLACEHOLDER",
    "token_id": "tok_01J9Z6PLACEHOLDER",
    "rule_id": "rul_01J9Z6PLACEHOLDER",
    "round_id": "rnd_01J9Z6PLACEHOLDER",
    "metric_id": "perf.placeholder",
}

#: 必填 query 参数的端点（矩阵里的通用请求需要补最小合法 query）。
_REQUIRED_QUERY: dict[str, str] = {
    "/reconciliation": "?period=2026-06",
    "/reconciliation/bridge": "?period=2026-06",
    # Period2-03：/nav 需要 period 参数才能通过 StrictQueryRoute 校验（避免 400 而非 401）
    "/nav": "?period=2026-06",
    # Period2-06：指标端点需 period 才能通过 query 校验
    "/metrics": "?period=2026-06",
    "/metrics/{metric_id}": "?period=2026-06",
    "/metrics/{metric_id}/explain": "?period=2026-06",
    "/metrics/{metric_id}/drilldown": "?period=2026-06",
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
    elif template == "/imports/{batch_id}/reclassify":
        kwargs["json"] = {}
    elif template == "/summary-rules" and method == "post":
        kwargs["json"] = {
            "name": "probe",
            "category": "other",
            "match_type": "exact",
            "pattern": "probe",
        }
    elif template == "/summary-rules/{rule_id}" and method == "patch":
        kwargs["headers"] = {"If-Match": 'W/"1"'}
        kwargs["json"] = {"name": "probe"}
    elif template == "/jobs/rebuild-rounds":
        kwargs["json"] = {"scope": "full"}
    elif template == "/assets/build":
        kwargs["headers"] = {"Idempotency-Key": "unauth-probe-" + "x" * 20}
    elif template == "/jobs/rebuild-period":
        kwargs["headers"] = {"Idempotency-Key": "unauth-probe-" + "x" * 20}
        kwargs["json"] = {"period": "2026-06"}
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
        "rule_id": await _seed_rule(owner_engine, user_id=owner.user_id),
        "round_id": await _seed_round(owner_engine, user_id=owner.user_id),
    }
    headers = dict(intruder.headers)
    json_body: dict[str, Any] | None = None
    if method == "patch":
        headers["If-Match"] = '"1"'
        json_body = (
            {"name": "跨租户越权尝试"}
            if template == "/summary-rules/{rule_id}"
            else {"note": "跨租户越权尝试"}
        )
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
    rule_id = await _seed_rule(owner_engine, user_id=owner.user_id)
    round_id = await _seed_round(owner_engine, user_id=owner.user_id)
    asset_date = await _seed_asset_row(owner_engine, user_id=owner.user_id)

    url = api_prefix + template + _REQUIRED_QUERY.get(template, "")
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

    if template == "/summary-rules":
        # 系统规则是共享代码资产（人人可见），私有规则必须只对本人可见。
        assert all(item["is_system"] for item in body["items"]), "B 的规则列表含 A 的私有规则"
        owner_resp = await _call(client, method, url, owner.headers)
        assert owner_resp.status_code == 200
        owner_ids = {item["rule_id"] for item in owner_resp.json()["items"]}
        assert rule_id in owner_ids, "A 自己的私有规则应可见（防止假阳性）"
        return

    if template == "/assets":
        # 资产行按 (user_id, trade_date) 隔离，且没有通用 id 字段：按 date 断言。
        assert body["items"] == [], "B 的资产表看到了 A 的行"
        owner_resp = await _call(client, method, url, owner.headers)
        assert owner_resp.status_code == 200, owner_resp.text
        owner_dates = {item["date"] for item in owner_resp.json()["items"]}
        assert asset_date.isoformat() in owner_dates, "A 自己的资产行应可见（防止假阳性）"
        return

    if template == "/dashboard":
        # 聚合对象：B 的持仓/已平仓计数为空；A 自己的回合计入（防止"两边都空"的假通过）。
        assert body["asset_overview"]["holdings"] == [], "B 的看板看到了 A 的持仓"
        assert body["history_overview"]["closed_round_count"] == 0, "B 的看板看到了 A 的回合"
        owner_resp = await _call(client, method, url, owner.headers)
        assert owner_resp.status_code == 200, owner_resp.text
        assert owner_resp.json()["history_overview"]["closed_round_count"] == 1, (
            "A 自己的回合应计入看板（防止假阳性）"
        )
        return

    if template == "/reconciliation/bridge":
        # 桥接为单对象：B 无任何 cash_balance → blocked 且 identity 全 0；
        # A 有自己的流水 → identity 非全 0（防止"两边都是 blocked"的假通过）。
        assert body["status"] == "blocked", body
        assert body["identity"]["initial_cash"] == "0.00"
        owner_resp = await _call(client, method, url, owner.headers)
        assert owner_resp.status_code == 200
        assert owner_resp.json()["identity"] != body["identity"], "A 的桥接结果应包含自己的数据"
        return

    id_field = {
        "/trades": "trade_id",
        "/imports": "batch_id",
        "/jobs": "job_id",
        "/summary-rules": "rule_id",
        "/rounds": "round_id",
    }[template]
    expected = {
        "trade_id": trade_id,
        "batch_id": batch_id,
        "job_id": job_id,
        "rule_id": rule_id,
        "round_id": round_id,
    }[id_field]
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
    url = api_prefix + template + _REQUIRED_QUERY.get(template, "")
    resp = await _call(client, method, url, admin.headers)
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
        method.upper(),
        api_prefix + _fill(template) + _REQUIRED_QUERY.get(template, ""),
        **_unauth_kwargs(method, template),
    )
    assert resp.status_code == 401, f"{method.upper()} {template} → {resp.status_code} {resp.text}"
    assert resp.json()["code"] == ErrorCode.TOKEN_EXPIRED.value
