"""API-082 验收：`POST /assets/build`（构建/增量更新资产表任务）。

覆盖设计 §9.3（API-082 AT-11~AT-19 / AT-25 / AT-26）：
* happy 首次：202 + Location/Retry-After + `type=build_assets`；drain 后 `GET /assets` 摘要齐全；
* 幂等：同 key 同 body 同一 job_id + `Idempotency-Replayed`（Location 仍在）；缺省 body 与
  `{"mode":"auto"}` 同 key 视为同请求；同 key 异体 → 422 `IDEMPOTENCY_KEY_REUSED`；
* 单飞：不同 key 同参复用（`X-Job-Reused`）；auto 运行中发 full → 409 `JOB_ALREADY_RUNNING`；
* 校验：缺/短 `Idempotency-Key` → 422、`mode=bad` → 422、4 个活跃任务后 → 429 `QUOTA_EXCEEDED`；
* 越权：read PAT → 403 INSUFFICIENT_SCOPE、admin → 403 ADMIN_CANNOT_READ_TRADES、无 token 401；
* 增量摘要：`last_mode=incremental`、`last_rows_written == 窗口内工作日数 < rows_count`，
  owner 直读 `platform.job.result` 校验键值，且增量结果与强制 full **逐行相等**；
* 无数据 / 无锚点：rows_count=0；无锚点时 build.state=blocked；
* job/SSE 契约：终态 `succeeded`/`stage=done`、`GET /jobs` 不扩 result 字段、SSE stage 合法且单调。

时钟口径：与 API-081 相同，用 `create_app(settings, clock=FrozenClock(...))` + 同钟 WorkerRunner。
"""

from __future__ import annotations

import itertools
import re
import uuid
from datetime import UTC, date, datetime, timedelta
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
_STAGE_RE = re.compile(r'"stage":"(queued|recomputing|writing|finalizing|done)"')
_PROGRESS_RE = re.compile(r'"progress":(\d+)')

# job.result 固定键（设计 §7.2；不扩 GET /jobs 响应）。
_RESULT_KEYS = {
    "mode",
    "rebuild_from",
    "rows_written",
    "rows_total",
    "built_through",
    "status",
    "assumption",
    "data_as_of",
}


# ── 确定性 app（FrozenClock）+ worker ────────────────────────


@pytest.fixture
def frozen_clock() -> FrozenClock:
    return FrozenClock(_BUILD_CLOCK)


@pytest_asyncio.fixture
async def assets_client(settings: Any, frozen_clock: FrozenClock) -> Any:
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
    """执行全部 pending 任务。

    `platform.job.available_at` 默认值来自 DB `now()`（真实时钟），而测试把 app/worker
    时钟固定在 2026-07-01；`claim` 的条件 `available_at <= clock.now()` 需要先把
    pending 任务的 available_at 拨到过去（测试侧时间注入的桥接）。
    """
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
    return f"assets-{uuid.uuid4().hex}"


def _workdays(start: date, end: date) -> int:
    """闭区间 [start, end] 内的工作日数（资产表周末不写行）。"""
    count = 0
    day = start
    while day <= end:
        if day.weekday() < 5:
            count += 1
        day += timedelta(days=1)
    return count


async def _post_build(
    client: httpx.AsyncClient,
    headers: dict[str, str],
    *,
    mode: str | None = None,
    key: str | None = None,
) -> httpx.Response:
    request_headers = {**headers, "Idempotency-Key": key or _idem_key()}
    kwargs: dict[str, Any] = {}
    if mode is not None:
        kwargs["json"] = {"mode": mode}
    return await client.post("/api/v1/assets/build", headers=request_headers, **kwargs)


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


async def _seed_long_history(engine: AsyncEngine, *, user_id: uuid.UUID) -> None:
    """> 45 天历史（2026-03-01 起）：用于增量窗口断言。

    旧流水的 `updated_at` 回拨到当天，模拟“很久以前导入”——否则首次 build 的水位线是
    “刚刚”，增量脏查询的 1h overlap 会把 03-01/03-02 的老流水也带进脏窗口，
    `rebuild_from` 会退到 start（等价全量），无法验证 45 天安全窗的最小写入。
    06-15 的余额快照是“最近一次导入”，作为水位线 → 脏窗口起点落在安全窗内。
    """
    old_1 = await _seed_trade(
        engine,
        user_id=user_id,
        trade_date=date(2026, 3, 1),
        side="buy",
        cash_balance_units=100_000_00,
    )
    old_2 = await _seed_trade(
        engine,
        user_id=user_id,
        trade_date=date(2026, 3, 2),
        side="buy",
        quantity_units=100_0000,
        price_units=1700_000000,
        amount_units=-170_000_00,
        cash_balance_units=-70_000_00,
        category="buy",
    )
    latest = await _seed_trade(
        engine,
        user_id=user_id,
        trade_date=date(2026, 6, 15),
        side="buy",
        cash_balance_units=-70_000_00,
    )
    for trade_id, when in (
        (old_1, datetime(2026, 3, 1, tzinfo=UTC)),
        (old_2, datetime(2026, 3, 2, tzinfo=UTC)),
        (latest, datetime(2026, 6, 15, tzinfo=UTC)),
    ):
        async with engine.begin() as conn:
            await conn.execute(
                text("update ledger.trade set updated_at = :ts where id = :id"),
                {"ts": when, "id": trade_id},
            )


async def _count_asset_rows(engine: AsyncEngine, *, user_id: uuid.UUID) -> int:
    async with engine.begin() as conn:
        return int(
            await conn.scalar(
                text("select count(*) from review.asset_daily where user_id = :uid"),
                {"uid": user_id},
            )
            or 0
        )


# ── AT-11 happy 首次（含 job/SSE 契约 AT-26）─────────────────


async def test_api_082_happy_first_build(
    assets_client: Any,
    account_factory: Any,
    owner_engine: AsyncEngine,
    settings: Any,
    frozen_clock: FrozenClock,
) -> None:
    acc = await account_factory()
    await _seed_happy_chain(owner_engine, user_id=acc.user_id)

    resp = await _post_build(assets_client, acc.headers)
    assert resp.status_code == 202, resp.text
    assert resp.headers["Location"].startswith("/api/v1/jobs/job_")
    assert resp.headers["Retry-After"] == "1"
    accepted = resp.json()
    assert accepted["type"] == "build_assets"
    assert accepted["status"] == "pending"
    assert accepted["links"]["self"].endswith(accepted["job_id"])
    assert accepted["links"]["events"].endswith(f"{accepted['job_id']}/events")
    assert "batch_id" not in accepted
    job_id = accepted["job_id"]

    await _drain_worker(settings, clock=frozen_clock, owner_engine=owner_engine)

    # owner 直读 job.result（固定键集合；GET /jobs 不扩 result 字段）
    job = await _job_row(owner_engine, job_id)
    assert job is not None
    assert job["status"] == "succeeded"
    assert job["stage"] == "done"
    result = job["result"]
    assert isinstance(result, dict)
    assert set(result) == _RESULT_KEYS, result
    assert result["mode"] == "full"
    assert result["status"] == "ok"
    assert result["assumption"] == "back_solved"
    assert result["built_through"] == "2026-07-01"
    # 05-29 起（含）→ 07-01：周末不写行，只统计工作日。
    expected_rows = _workdays(date(2026, 5, 29), date(2026, 7, 1))
    assert result["rows_written"] == result["rows_total"] == expected_rows

    job_detail = await assets_client.get(f"/api/v1/jobs/{job_id}", headers=acc.headers)
    assert job_detail.status_code == 200
    assert "result" not in job_detail.json(), "GET /jobs 不应新增 result 字段"
    assert job_detail.json()["status"] == "succeeded"
    assert job_detail.json()["stage"] == "done"

    # 行可见 + build 摘要齐全
    listed = await assets_client.get("/api/v1/assets", headers=acc.headers)
    assert listed.status_code == 200
    body = listed.json()
    assert body["items"], "drain 后应可见新行"
    build = body["build"]
    assert build["last_mode"] == "full"
    assert build["last_rows_written"] == expected_rows
    assert build["last_built_through"] == "2026-07-01"
    assert build["rows_count"] == expected_rows
    assert build["state"] == "current"
    assert body["data_as_of"] == "2026-09-30"

    # AT-26：SSE 终态 done；handler 阶段的 stage 合法、progress 单调
    events = await assets_client.get(f"/api/v1/jobs/{job_id}/events", headers=acc.headers)
    assert events.status_code == 200
    assert events.headers["content-type"].startswith("text/event-stream")
    stream = events.text
    assert "event: done" in stream
    assert '"status":"succeeded"' in stream
    stages = _STAGE_RE.findall(stream)
    assert stages, stream
    handler_stages = [
        stage for stage in stages if stage in {"recomputing", "writing", "finalizing"}
    ]
    assert handler_stages == ["recomputing", "writing", "finalizing"], stream
    progressed = [int(value) for value in _PROGRESS_RE.findall(stream)]
    assert progressed == sorted(progressed), progressed
    # 平台 finish 会把终态事件也带上 progress=100，去重后才是 handler 上报序列（粒度 ≥5）。
    deduped = [
        value
        for index, value in enumerate(progressed)
        if index == 0 or value != progressed[index - 1]
    ]
    assert deduped == [10, 80, 100], progressed
    assert all(later - earlier >= 5 for earlier, later in itertools.pairwise(deduped))


# ── AT-12 / AT-19 / AT-25 幂等 ──────────────────────────────


async def test_api_082_idempotent_replay(
    assets_client: Any,
    account_factory: Any,
    owner_engine: AsyncEngine,
    settings: Any,
    frozen_clock: FrozenClock,
) -> None:
    acc = await account_factory()
    await _seed_happy_chain(owner_engine, user_id=acc.user_id)
    key = _idem_key()

    first = await _post_build(assets_client, acc.headers, key=key)
    assert first.status_code == 202, first.text
    job_id = first.json()["job_id"]

    # 缺省 body 与 {"mode":"auto"} 同 key 视为同一请求（不能 422 IDEMPOTENCY_KEY_REUSED）
    replay = await _post_build(assets_client, acc.headers, mode="auto", key=key)
    assert replay.status_code == 202, replay.text
    assert replay.json()["job_id"] == job_id
    assert replay.headers.get("Idempotency-Replayed") == "true"
    assert replay.headers["Location"] == f"/api/v1/jobs/{job_id}"
    assert replay.headers["Retry-After"] == "1"

    await _drain_worker(settings, clock=frozen_clock, owner_engine=owner_engine)
    rows_after_first = await _count_asset_rows(owner_engine, user_id=acc.user_id)

    # 重放不重复执行、不重复写行
    again = await _post_build(assets_client, acc.headers, key=key)
    assert again.status_code == 202
    assert again.json()["job_id"] == job_id
    assert again.headers.get("Idempotency-Replayed") == "true"
    await _drain_worker(settings, clock=frozen_clock, owner_engine=owner_engine)
    assert await _count_asset_rows(owner_engine, user_id=acc.user_id) == rows_after_first

    # 同 key 异体 → 422
    conflicting = await _post_build(assets_client, acc.headers, mode="full", key=key)
    assert conflicting.status_code == 422, conflicting.text
    assert conflicting.json()["code"] == ErrorCode.IDEMPOTENCY_KEY_REUSED.value


# ── AT-13 单飞 ─────────────────────────────────────────────


async def test_api_082_single_flight(
    assets_client: Any,
    account_factory: Any,
    owner_engine: AsyncEngine,
) -> None:
    acc = await account_factory()
    await _seed_happy_chain(owner_engine, user_id=acc.user_id)

    first = await _post_build(assets_client, acc.headers)
    second = await _post_build(assets_client, acc.headers)
    assert first.status_code == 202, first.text
    assert second.status_code == 202, second.text
    assert second.json()["job_id"] == first.json()["job_id"]
    assert second.headers.get("X-Job-Reused") == "true"

    # 同 scope 异参（auto 运行中发 full）→ 409
    full = await _post_build(assets_client, acc.headers, mode="full")
    assert full.status_code == 409, full.text
    assert full.json()["code"] == ErrorCode.JOB_ALREADY_RUNNING.value


# ── AT-15 校验 / 配额 ──────────────────────────────────────


async def test_api_082_validation_errors(assets_client: Any, account_factory: Any) -> None:
    acc = await account_factory()

    missing = await assets_client.post("/api/v1/assets/build", headers=acc.headers)
    assert missing.status_code == 422, missing.text
    assert missing.json()["code"] == ErrorCode.VALIDATION_ERROR.value

    short_key = await assets_client.post(
        "/api/v1/assets/build",
        headers={**acc.headers, "Idempotency-Key": "short"},
    )
    assert short_key.status_code == 422
    assert short_key.json()["code"] == ErrorCode.VALIDATION_ERROR.value

    bad_mode = await assets_client.post(
        "/api/v1/assets/build",
        headers={**acc.headers, "Idempotency-Key": _idem_key()},
        json={"mode": "bad"},
    )
    assert bad_mode.status_code == 422
    assert bad_mode.json()["code"] == ErrorCode.VALIDATION_ERROR.value


async def test_api_082_quota_exceeded(
    assets_client: Any,
    client: httpx.AsyncClient,
    account_factory: Any,
    owner_engine: AsyncEngine,
    settings: Any,
) -> None:
    """用户已有 4 个不同 scope 的活跃任务 → 再入队 429 QUOTA_EXCEEDED。"""
    acc = await account_factory()
    await _seed_happy_chain(owner_engine, user_id=acc.user_id)
    for month in ("2026-01", "2026-02", "2026-03", "2026-04"):
        created = await client.post(
            "/api/v1/jobs/rebuild-rounds",
            headers={**acc.headers, "Idempotency-Key": _idem_key()},
            json={"scope": "period", "period": month},
        )
        assert created.status_code == 202, created.text

    resp = await _post_build(assets_client, acc.headers)
    assert resp.status_code == 429, resp.text
    assert resp.json()["code"] == ErrorCode.QUOTA_EXCEEDED.value


# ── AT-14 越权 ────────────────────────────────────────────


async def test_api_082_authz(
    assets_client: Any,
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

    read_pat = await assets_client.post(
        "/api/v1/assets/build",
        headers={"Authorization": f"Bearer {pat}", "Idempotency-Key": _idem_key()},
    )
    assert read_pat.status_code == 403, read_pat.text
    assert read_pat.json()["code"] == ErrorCode.INSUFFICIENT_SCOPE.value

    admin = await account_factory(role="admin")
    admin_resp = await assets_client.post(
        "/api/v1/assets/build",
        headers={**admin.headers, "Idempotency-Key": _idem_key()},
    )
    assert admin_resp.status_code == 403, admin_resp.text
    assert admin_resp.json()["code"] == ErrorCode.ADMIN_CANNOT_READ_TRADES.value

    unauth = await assets_client.post(
        "/api/v1/assets/build", headers={"Idempotency-Key": _idem_key()}
    )
    assert unauth.status_code == 401, unauth.text
    assert unauth.json()["code"] == ErrorCode.TOKEN_EXPIRED.value


# ── AT-16 增量摘要 + 与 full 逐行相等 ──────────────────────


async def test_api_082_incremental_summary_equals_full(
    assets_client: Any,
    account_factory: Any,
    owner_engine: AsyncEngine,
    settings: Any,
    frozen_clock: FrozenClock,
) -> None:
    acc = await account_factory()
    await _seed_long_history(owner_engine, user_id=acc.user_id)

    full = await _post_build(assets_client, acc.headers, mode="full")
    assert full.status_code == 202, full.text
    first_job = full.json()["job_id"]
    await _drain_worker(settings, clock=frozen_clock, owner_engine=owner_engine)

    first_result = await _job_row(owner_engine, first_job)
    assert first_result is not None and isinstance(first_result["result"], dict)
    rows_total = first_result["result"]["rows_total"]
    expected_total = _workdays(date(2026, 3, 1), date(2026, 7, 1))
    assert rows_total == expected_total

    before = await assets_client.get("/api/v1/assets", params={"limit": 200}, headers=acc.headers)
    assert before.status_code == 200
    rows_before = before.json()["items"]
    assert len(rows_before) == rows_total

    # 追加一条**改变窗口内持仓**的新流水（version=1 → 仍是增量）
    await _seed_trade(
        owner_engine,
        user_id=acc.user_id,
        trade_date=date(2026, 6, 30),
        side="buy",
        quantity_units=10_0000,
        price_units=1700_000000,
        amount_units=-17_000_00,
        category="buy",
    )

    stale = await assets_client.get("/api/v1/assets", headers=acc.headers)
    assert stale.json()["build"]["state"] == "stale"

    incremental = await _post_build(assets_client, acc.headers, mode="auto")
    assert incremental.status_code == 202, incremental.text
    incremental_job = incremental.json()["job_id"]
    await _drain_worker(settings, clock=frozen_clock, owner_engine=owner_engine)

    job = await _job_row(owner_engine, incremental_job)
    assert job is not None and isinstance(job["result"], dict)
    result = job["result"]
    assert result["mode"] == "incremental"
    assert result["rebuild_from"] == "2026-05-18"
    # 安全窗 45 天（calendar 口径）：rebuild_from = end - 44；窗口内只写工作日。
    expected_incremental = _workdays(date(2026, 5, 18), date(2026, 7, 1))
    assert result["rows_written"] == expected_incremental
    assert result["rows_total"] == rows_total
    assert result["built_through"] == "2026-07-01"

    after = await assets_client.get("/api/v1/assets", params={"limit": 200}, headers=acc.headers)
    body = after.json()
    build = body["build"]
    assert build["last_mode"] == "incremental"
    assert build["last_rebuild_from"] == "2026-05-18"
    assert build["last_rows_written"] == expected_incremental
    assert build["last_rows_written"] < build["rows_count"] == rows_total
    rows_incremental = body["items"]
    assert len(rows_incremental) == rows_total

    # 增量结果与强制 full 逐行相等（排除 rebuilt_at 等审计列——响应本就不含）
    forced_full = await _post_build(assets_client, acc.headers, mode="full")
    assert forced_full.status_code == 202, forced_full.text
    await _drain_worker(settings, clock=frozen_clock, owner_engine=owner_engine)
    full_again = await assets_client.get(
        "/api/v1/assets", params={"limit": 200}, headers=acc.headers
    )
    assert full_again.status_code == 200
    assert full_again.json()["items"] == rows_incremental, "增量结果与全量结果不一致"
    assert full_again.json()["build"]["last_mode"] == "full"


# ── AT-18 无数据 / 无锚点 ─────────────────────────────────


async def test_api_082_no_data_is_ok(
    assets_client: Any,
    account_factory: Any,
    owner_engine: AsyncEngine,
    settings: Any,
    frozen_clock: FrozenClock,
) -> None:
    acc = await account_factory()
    resp = await _post_build(assets_client, acc.headers)
    assert resp.status_code == 202, resp.text
    job_id = resp.json()["job_id"]
    await _drain_worker(settings, clock=frozen_clock, owner_engine=owner_engine)

    job = await _job_row(owner_engine, job_id)
    assert job is not None and isinstance(job["result"], dict)
    assert job["status"] == "succeeded"
    assert job["result"]["rows_total"] == 0
    assert job["result"]["status"] == "ok"

    listed = await assets_client.get("/api/v1/assets", headers=acc.headers)
    body = listed.json()
    assert body["items"] == []
    assert body["build"]["state"] == "missing"
    assert body["build"]["rows_count"] == 0
    assert await _count_asset_rows(owner_engine, user_id=acc.user_id) == 0


async def test_api_082_no_anchor_is_blocked(
    assets_client: Any,
    account_factory: Any,
    owner_engine: AsyncEngine,
    settings: Any,
    frozen_clock: FrozenClock,
) -> None:
    acc = await account_factory()
    await _seed_trade(
        owner_engine,
        user_id=acc.user_id,
        trade_date=date(2026, 6, 1),
        side="buy",
        quantity_units=100_0000,
        price_units=1700_000000,
        amount_units=-170_000_00,
        category="buy",
    )
    resp = await _post_build(assets_client, acc.headers)
    assert resp.status_code == 202, resp.text
    job_id = resp.json()["job_id"]
    await _drain_worker(settings, clock=frozen_clock, owner_engine=owner_engine)

    job = await _job_row(owner_engine, job_id)
    assert job is not None and isinstance(job["result"], dict)
    assert job["status"] == "succeeded"
    assert job["result"]["status"] == "blocked"
    assert job["result"]["rows_total"] == 0

    listed = await assets_client.get("/api/v1/assets", headers=acc.headers)
    body = listed.json()
    assert body["items"] == []
    assert body["build"]["state"] == "blocked"
    assert body["build"]["last_status"] == "blocked"
    assert body["build"]["status_reason"]
    assert await _count_asset_rows(owner_engine, user_id=acc.user_id) == 0
