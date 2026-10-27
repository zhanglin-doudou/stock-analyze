"""API-083 验收：`POST /jobs/rebuild-period`（按月一键编排 rounds → assets → nav → metrics）。

覆盖 API-Catalog §2 API-083 验收断言：
* happy：202 + Location/Retry-After + `type=rebuild_period`；drain 后 job.result 四阶段键
  齐全（rounds/assets/nav/metrics），核心 10 指标全部物化进 `review.metric_result`
  （含 blocked/stale 状态行，force=True 构建路径）；
* exploratory：`include_exploratory=true` → 18 个指标全量物化；
* 幂等：同 key 同 body 重放 `Idempotency-Replayed`（缺省 false 与显式 false 同请求）；
  同 key 异体 → 422 `IDEMPOTENCY_KEY_REUSED`；
* 单飞：不同 key 同参复用（`X-Job-Reused`）；同 period 运行中翻 include_exploratory →
  409 `JOB_ALREADY_RUNNING`；
* 校验：缺/短 `Idempotency-Key` → 422、`period` 格式非法/未来月 → 422 `INVALID_PERIOD`；
* 越权：read PAT → 403 INSUFFICIENT_SCOPE、admin → 403 ADMIN_CANNOT_READ_TRADES、
  无 token 401；
* job/SSE 契约：终态 `succeeded`/`stage=done`、阶段序列 rounds→assets→nav→metrics、
  progress 单调不减、`GET /jobs` 不扩 result 字段。

时钟口径：与 API-082 相同，`create_app(settings, clock=FrozenClock(...))` + 同钟 WorkerRunner；
period 取冻结时钟的上一个月（2026-06），未来月校验以冻结时钟为准。
"""

from __future__ import annotations

import itertools
import re
import uuid
from datetime import UTC, date, datetime
from typing import Any

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession
from stock_api.app import create_app
from stock_contracts.clock import FrozenClock
from stock_contracts.errors import ErrorCode
from stock_ledger.models import Trade
from stock_worker.runner import WorkerRunner

pytestmark = pytest.mark.api

_BUILD_CLOCK = "2026-07-01T09:00:00+00:00"
_PERIOD = "2026-06"
_STAGE_RE = re.compile(r'"stage":"(queued|rounds|assets|nav|metrics|done)"')
_PROGRESS_RE = re.compile(r'"progress":(\d+)')

_RESULT_KEYS = {"period", "include_exploratory", "rounds", "assets", "nav", "metrics"}
_CORE_METRIC_COUNT = 10
_ALL_METRIC_COUNT = 18


# ── 确定性 app（FrozenClock）+ worker ────────────────────────


@pytest.fixture
def frozen_clock() -> FrozenClock:
    return FrozenClock(_BUILD_CLOCK)


@pytest_asyncio.fixture
async def period_client(settings: Any, frozen_clock: FrozenClock) -> Any:
    app = create_app(settings, clock=frozen_clock)
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client


async def _drain_worker(
    settings: Any,
    *,
    clock: FrozenClock | None = None,
    owner_engine: AsyncEngine,
) -> None:
    """执行全部 pending 任务（available_at 拨到过去，桥接测试侧时间注入）。"""
    async with owner_engine.begin() as conn:
        await conn.execute(
            text("update platform.job set available_at = :ts where status = 'pending'"),
            {"ts": datetime(2000, 1, 1, tzinfo=UTC)},
        )
    runner = WorkerRunner(settings, clock=clock)
    try:
        while True:
            claimed = await runner._claim_one()
            if claimed is None:
                break
            await runner._execute(claimed)
    finally:
        await runner.close()


def _idem_key() -> str:
    return f"rebuild-period-{uuid.uuid4().hex}"


async def _post_rebuild(
    client: httpx.AsyncClient,
    headers: dict[str, str],
    *,
    period: str = _PERIOD,
    include_exploratory: bool | None = None,
    key: str | None = None,
) -> httpx.Response:
    request_headers = {**headers, "Idempotency-Key": key or _idem_key()}
    body: dict[str, Any] = {"period": period}
    if include_exploratory is not None:
        body["include_exploratory"] = include_exploratory
    return await client.post("/api/v1/jobs/rebuild-period", headers=request_headers, json=body)


async def _job_row(engine: AsyncEngine, job_id: str) -> dict[str, Any] | None:
    async with engine.begin() as conn:
        row = (
            (
                await conn.execute(
                    text(
                        "select status, stage, attempt, error_code, error, result "
                        "from platform.job where id = :id"
                    ),
                    {"id": job_id},
                )
            )
            .mappings()
            .first()
        )
    return dict(row) if row is not None else None


async def _metric_rows(
    engine: AsyncEngine, *, user_id: uuid.UUID, period: str
) -> list[dict[str, Any]]:
    async with engine.begin() as conn:
        rows = (
            (
                await conn.execute(
                    text(
                        "select metric_id, status, value_units, reason "
                        "from review.metric_result where user_id = :uid and period = :period"
                    ),
                    {"uid": user_id, "period": period},
                )
            )
            .mappings()
            .all()
        )
    return [dict(row) for row in rows]


# ── 造数 ────────────────────────────────────────────────────


async def _seed_trade(
    engine: AsyncEngine,
    *,
    user_id: uuid.UUID,
    trade_date: date,
    side: str,
    quantity_units: int = 0,
    price_units: int = 0,
    amount_units: int = 0,
    cash_balance_units: int | None = None,
    category: str = "other",
    security_code: str = "600519",
    summary: str = "证券买入",
) -> str:
    trade_id = f"trd_{uuid.uuid4().hex[:12]}"
    async with AsyncSession(engine) as session:
        session.add(
            Trade(
                id=trade_id,
                user_id=user_id,
                batch_id=None,
                trade_date=trade_date,
                security_code=security_code,
                security_name=security_code,
                side=side,
                quantity_units=quantity_units,
                price_units=price_units,
                amount_units=amount_units,
                commission_units=0,
                stamp_tax_units=0,
                other_fee_units=0,
                cash_balance_units=cash_balance_units,
                cash_balance_available=cash_balance_units is not None,
                category=category,
                occurrence_idx=0,
                source="import",
                version=1,
                summary=summary,
                dedupe_key=f"{security_code}:{uuid.uuid4().hex[:10]}",
            )
        )
        await session.commit()
    return trade_id


async def _seed_happy_chain(engine: AsyncEngine, *, user_id: uuid.UUID) -> None:
    """现金锚点（05-29）+ 一笔真实买入（06-30）：四阶段都有产出。"""
    await _seed_trade(
        engine,
        user_id=user_id,
        trade_date=date(2026, 5, 29),
        side="buy",
        cash_balance_units=100_000_00,
    )
    await _seed_trade(
        engine,
        user_id=user_id,
        trade_date=date(2026, 6, 30),
        side="buy",
        quantity_units=100_0000,
        price_units=1700_000000,
        amount_units=-170_000_00,
        cash_balance_units=-70_000_00,
        category="buy",
    )


# ── happy：四阶段编排 + 物化 + job/SSE 契约 ─────────────────


async def test_api_083_happy_orchestration(
    period_client: Any,
    account_factory: Any,
    owner_engine: AsyncEngine,
    settings: Any,
    frozen_clock: FrozenClock,
) -> None:
    acc = await account_factory()
    await _seed_happy_chain(owner_engine, user_id=acc.user_id)

    resp = await _post_rebuild(period_client, acc.headers)
    assert resp.status_code == 202, resp.text
    assert resp.headers["Location"].startswith("/api/v1/jobs/job_")
    assert resp.headers["Retry-After"] == "1"
    accepted = resp.json()
    assert accepted["type"] == "rebuild_period"
    assert accepted["status"] == "pending"
    assert accepted["links"]["self"].endswith(accepted["job_id"])
    assert accepted["links"]["events"].endswith(f"{accepted['job_id']}/events")
    job_id = accepted["job_id"]

    await _drain_worker(settings, clock=frozen_clock, owner_engine=owner_engine)

    job = await _job_row(owner_engine, job_id)
    assert job is not None
    assert job["status"] == "succeeded", job
    assert job["stage"] == "done"
    result = job["result"]
    assert isinstance(result, dict)
    assert set(result) == _RESULT_KEYS, result
    assert result["period"] == _PERIOD
    assert result["include_exploratory"] is False

    # 阶段①rounds：窗口内证券全历史重建 → 一笔买入形成一个 open 回合
    assert set(result["rounds"]) == {"rounds_total", "rounds_closed", "rounds_open"}
    assert result["rounds"]["rounds_total"] == 1
    assert result["rounds"]["rounds_open"] == 1

    # 阶段②assets：首次构建 full，05-29..07-01 共 34 行
    assert set(result["assets"]) == {"mode", "rows_written", "status", "data_as_of"}
    assert result["assets"]["mode"] == "full"
    assert result["assets"]["status"] == "ok"
    assert result["assets"]["rows_written"] == 34
    assert result["assets"]["data_as_of"] == "2026-09-30"

    # 阶段③nav：消费同事务 asset_daily
    assert set(result["nav"]) == {"status"}
    assert result["nav"]["status"] == "ok"

    # 阶段④metrics：默认核心 10，状态矩阵全量物化（含 blocked/stale）
    metrics = result["metrics"]
    assert set(metrics) == {"computed", "persisted", "statuses", "data_as_of"}
    assert metrics["computed"] == _CORE_METRIC_COUNT
    assert metrics["persisted"] == _CORE_METRIC_COUNT
    assert sum(metrics["statuses"].values()) == _CORE_METRIC_COUNT
    assert set(metrics["statuses"]) <= {"ok", "blocked", "stale", "unreliable"}
    assert metrics["data_as_of"] == "2026-06-30"

    # 物化行：该 period 恰 10 个不同指标；非 ok 行 value NULL + reason 非空（DB CHECK）
    rows = await _metric_rows(owner_engine, user_id=acc.user_id, period=_PERIOD)
    assert len(rows) == _CORE_METRIC_COUNT
    assert len({row["metric_id"] for row in rows}) == _CORE_METRIC_COUNT
    for row in rows:
        assert row["status"] in {"ok", "blocked", "stale", "unreliable"}
        if row["status"] != "ok":
            assert row["value_units"] is None
            assert row["reason"]

    # GET /jobs 不扩 result 字段
    job_detail = await period_client.get(f"/api/v1/jobs/{job_id}", headers=acc.headers)
    assert job_detail.status_code == 200
    assert "result" not in job_detail.json()
    assert job_detail.json()["status"] == "succeeded"
    assert job_detail.json()["stage"] == "done"

    # SSE：阶段序列 rounds→assets→nav→metrics，progress 单调、终态 done
    events = await period_client.get(f"/api/v1/jobs/{job_id}/events", headers=acc.headers)
    assert events.status_code == 200
    stream = events.text
    assert "event: done" in stream
    assert '"status":"succeeded"' in stream
    stages = _STAGE_RE.findall(stream)
    handler_stages = [s for s in stages if s in {"rounds", "assets", "nav", "metrics"}]
    assert handler_stages == ["rounds", "assets", "nav", "metrics"], stream
    progressed = [int(value) for value in _PROGRESS_RE.findall(stream)]
    assert progressed == sorted(progressed), progressed
    deduped = [
        value
        for index, value in enumerate(progressed)
        if index == 0 or value != progressed[index - 1]
    ]
    assert deduped == [10, 40, 60, 85, 100], progressed
    assert all(later - earlier >= 5 for earlier, later in itertools.pairwise(deduped))


# ── exploratory：include_exploratory=true → 18 全量 ─────────


async def test_api_083_include_exploratory_persists_all(
    period_client: Any,
    account_factory: Any,
    owner_engine: AsyncEngine,
    settings: Any,
    frozen_clock: FrozenClock,
) -> None:
    acc = await account_factory()
    await _seed_happy_chain(owner_engine, user_id=acc.user_id)

    resp = await _post_rebuild(period_client, acc.headers, include_exploratory=True)
    assert resp.status_code == 202, resp.text
    job_id = resp.json()["job_id"]

    await _drain_worker(settings, clock=frozen_clock, owner_engine=owner_engine)

    job = await _job_row(owner_engine, job_id)
    assert job is not None and job["status"] == "succeeded", job
    result = job["result"]
    assert result["include_exploratory"] is True
    assert result["metrics"]["computed"] == _ALL_METRIC_COUNT
    assert result["metrics"]["persisted"] == _ALL_METRIC_COUNT
    assert sum(result["metrics"]["statuses"].values()) == _ALL_METRIC_COUNT

    rows = await _metric_rows(owner_engine, user_id=acc.user_id, period=_PERIOD)
    assert len(rows) == _ALL_METRIC_COUNT
    assert len({row["metric_id"] for row in rows}) == _ALL_METRIC_COUNT


# ── 幂等 ────────────────────────────────────────────────────


async def test_api_083_idempotent_replay(
    period_client: Any,
    account_factory: Any,
    owner_engine: AsyncEngine,
    settings: Any,
    frozen_clock: FrozenClock,
) -> None:
    acc = await account_factory()
    await _seed_happy_chain(owner_engine, user_id=acc.user_id)
    key = _idem_key()

    first = await _post_rebuild(period_client, acc.headers, key=key)
    assert first.status_code == 202, first.text
    job_id = first.json()["job_id"]

    # 显式 include_exploratory=false 与缺省视为同一请求（不能 422）
    replay = await _post_rebuild(period_client, acc.headers, include_exploratory=False, key=key)
    assert replay.status_code == 202, replay.text
    assert replay.json()["job_id"] == job_id
    assert replay.headers.get("Idempotency-Replayed") == "true"
    assert replay.headers["Location"] == f"/api/v1/jobs/{job_id}"
    assert replay.headers["Retry-After"] == "1"

    await _drain_worker(settings, clock=frozen_clock, owner_engine=owner_engine)
    rows_after_first = await _metric_rows(owner_engine, user_id=acc.user_id, period=_PERIOD)

    # 重放不重复执行、不重复写行
    again = await _post_rebuild(period_client, acc.headers, key=key)
    assert again.status_code == 202
    assert again.json()["job_id"] == job_id
    assert again.headers.get("Idempotency-Replayed") == "true"
    await _drain_worker(settings, clock=frozen_clock, owner_engine=owner_engine)
    assert len(await _metric_rows(owner_engine, user_id=acc.user_id, period=_PERIOD)) == len(
        rows_after_first
    )

    # 同 key 异体 → 422
    conflicting = await _post_rebuild(period_client, acc.headers, include_exploratory=True, key=key)
    assert conflicting.status_code == 422, conflicting.text
    assert conflicting.json()["code"] == ErrorCode.IDEMPOTENCY_KEY_REUSED.value


# ── 单飞 ────────────────────────────────────────────────────


async def test_api_083_single_flight(
    period_client: Any,
    account_factory: Any,
    owner_engine: AsyncEngine,
) -> None:
    acc = await account_factory()
    await _seed_happy_chain(owner_engine, user_id=acc.user_id)

    first = await _post_rebuild(period_client, acc.headers)
    second = await _post_rebuild(period_client, acc.headers)
    assert first.status_code == 202, first.text
    assert second.status_code == 202, second.text
    assert second.json()["job_id"] == first.json()["job_id"]
    assert second.headers.get("X-Job-Reused") == "true"

    # 同 period 运行中翻 include_exploratory → 409
    flipped = await _post_rebuild(period_client, acc.headers, include_exploratory=True)
    assert flipped.status_code == 409, flipped.text
    assert flipped.json()["code"] == ErrorCode.JOB_ALREADY_RUNNING.value

    # 不同 period 是不同 scope，不受单飞影响
    other = await _post_rebuild(period_client, acc.headers, period="2026-05")
    assert other.status_code == 202, other.text
    assert other.json()["job_id"] != first.json()["job_id"]


# ── 校验：幂等键 / period ───────────────────────────────────


async def test_api_083_validation_errors(period_client: Any, account_factory: Any) -> None:
    acc = await account_factory()

    missing = await period_client.post(
        "/api/v1/jobs/rebuild-period", headers=acc.headers, json={"period": _PERIOD}
    )
    assert missing.status_code == 422, missing.text
    assert missing.json()["code"] == ErrorCode.VALIDATION_ERROR.value

    short_key = await period_client.post(
        "/api/v1/jobs/rebuild-period",
        headers={**acc.headers, "Idempotency-Key": "short"},
        json={"period": _PERIOD},
    )
    assert short_key.status_code == 422
    assert short_key.json()["code"] == ErrorCode.VALIDATION_ERROR.value

    bad_format = await _post_rebuild(period_client, acc.headers, period="2026-13")
    assert bad_format.status_code == 422, bad_format.text
    assert bad_format.json()["code"] == ErrorCode.INVALID_PERIOD.value

    # 冻结时钟 2026-07-01：2026-08 是未来月
    future = await _post_rebuild(period_client, acc.headers, period="2026-08")
    assert future.status_code == 422, future.text
    assert future.json()["code"] == ErrorCode.INVALID_PERIOD.value


# ── 越权 ────────────────────────────────────────────────────


async def test_api_083_authz(
    period_client: Any,
    client: httpx.AsyncClient,
    account_factory: Any,
) -> None:
    user = await account_factory()
    token_resp = await client.post(
        "/api/v1/tokens",
        headers=user.headers,
        json={"name": "read-only", "scopes": ["read"]},
    )
    assert token_resp.status_code == 201, token_resp.text
    pat = token_resp.json()["token"]["token"]

    read_pat = await period_client.post(
        "/api/v1/jobs/rebuild-period",
        headers={"Authorization": f"Bearer {pat}", "Idempotency-Key": _idem_key()},
        json={"period": _PERIOD},
    )
    assert read_pat.status_code == 403, read_pat.text
    assert read_pat.json()["code"] == ErrorCode.INSUFFICIENT_SCOPE.value

    admin = await account_factory(role="admin")
    admin_resp = await period_client.post(
        "/api/v1/jobs/rebuild-period",
        headers={**admin.headers, "Idempotency-Key": _idem_key()},
        json={"period": _PERIOD},
    )
    assert admin_resp.status_code == 403, admin_resp.text
    assert admin_resp.json()["code"] == ErrorCode.ADMIN_CANNOT_READ_TRADES.value

    unauth = await period_client.post(
        "/api/v1/jobs/rebuild-period",
        headers={"Idempotency-Key": _idem_key()},
        json={"period": _PERIOD},
    )
    assert unauth.status_code == 401, unauth.text
    assert unauth.json()["code"] == ErrorCode.TOKEN_EXPIRED.value
