"""API-097 验收：`POST /settings/trade-flags/rebuild`（按生效阈值重建账单旗标）。

覆盖：
* happy：202 + Location/Retry-After + `type/status/links`；drain 后 `job.result` 含
  `months/months_count/trades/rows/flags_hash/truncated`，库内行数一致且指纹=当前阈值；
* 幂等：同 key 同 body → 同一 job_id + `Idempotency-Replayed`；同 key 异体 → 422；
* 单飞：不同 key 同参复用（`X-Job-Reused`）；同 scope 异参在途 → 409 `JOB_ALREADY_RUNNING`；
* 校验：缺/短 `Idempotency-Key`、`scope=all` 带 months、`scope=months` 空 months、
  非 `YYYY-MM`、超过 120 个月 → 422；
* `scope=all`：按首末交易月逐月重建；
* 偏好联动（P1-1 + P1-2）：改阈值后看板 `stale_months` 非空，调用重建后清空；
* 越权：read PAT → 403、无 token → 401；跨用户重建互不影响。

时钟固定 2026-07-15（东八区），看板最新完整月 = 2026-06。
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
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
from stock_contracts.preferences import DEFAULT_FLAGS_HASH
from stock_ledger.models import Trade
from stock_platform.ports import Bar, MarketCapabilities, Quote
from stock_worker.runner import WorkerRunner

pytestmark = pytest.mark.api

API_PREFIX = "/api/v1"
_REBUILD_PATH = f"{API_PREFIX}/settings/trade-flags/rebuild"
_CLOCK_TEXT = "2026-07-15T09:00:00+08:00"
SYMBOL = "600000"
BENCH = "hs300_tr"
CALENDAR = tuple(date(2026, 4, 1) + timedelta(days=index) for index in range(120))


class _FakeMarket:
    """回放行情桩：覆盖 2026-04 ~ 2026-07 的固定收盘价（净价 10.00）。"""

    capabilities = MarketCapabilities(mode="replay", symbols=(SYMBOL, BENCH))

    @property
    def as_of(self) -> date:
        return CALENDAR[-1]

    @property
    def provenance(self) -> str:
        return "test"

    async def get_daily_bars(self, symbols: Sequence[str], start: date, end: date) -> Sequence[Bar]:
        return [
            Bar(symbol=symbol, trade_date=day, close_units=10_000_000, return_units=0)
            for symbol in symbols
            for day in CALENDAR
            if start <= day <= end
        ]

    async def get_latest_quotes(self, symbols: Sequence[str]) -> Sequence[Quote]:
        return ()


@pytest.fixture
def frozen_clock() -> FrozenClock:
    return FrozenClock(_CLOCK_TEXT)


@pytest_asyncio.fixture
async def rebuild_client(settings: Any, frozen_clock: FrozenClock) -> Any:
    app = create_app(settings, clock=frozen_clock)
    app.state.sa.market_data = _FakeMarket()  # type: ignore[assignment]
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client


async def _drain_worker(settings: Any, *, clock: FrozenClock, owner_engine: AsyncEngine) -> None:
    """执行全部 pending 任务（把 DB `now()` 生成的 available_at 拨到过去）。"""
    async with owner_engine.begin() as conn:
        await conn.execute(
            text("update platform.job set available_at = :ts where status = 'pending'"),
            {"ts": datetime(2000, 1, 1, tzinfo=UTC)},
        )
    runner = WorkerRunner(settings, clock=clock)
    runner._market_data = _FakeMarket()
    try:
        while True:
            claimed = await runner._claim_one()
            if claimed is None:
                break
            await runner._execute(claimed)
    finally:
        await runner.close()


def _idem_key() -> str:
    return f"trade-flags-{uuid.uuid4().hex}"


async def _post(
    client: httpx.AsyncClient,
    headers: dict[str, str],
    *,
    body: dict[str, Any] | None = None,
    key: str | None = None,
) -> httpx.Response:
    kwargs: dict[str, Any] = {}
    if body is not None:
        kwargs["json"] = body
    return await client.post(
        _REBUILD_PATH,
        headers={**headers, "Idempotency-Key": key or _idem_key()},
        **kwargs,
    )


async def _seed_trade(
    engine: AsyncEngine,
    *,
    user_id: uuid.UUID,
    trade_date: date,
    line_no: int = 1,
    side: str = "buy",
    quantity_units: int = 1000,
    amount_units: int = 100,
) -> str:
    """净价 = 10.00（amount 1.00 / qty 0.1），与行情桩收盘价一致。"""
    trade_id = f"trd_{uuid.uuid4().hex[:12]}"
    async with AsyncSession(engine) as session:
        session.add(
            Trade(
                id=trade_id,
                user_id=user_id,
                batch_id=None,
                trade_date=trade_date,
                security_code=SYMBOL,
                security_name="测试股",
                side=side,
                quantity_units=quantity_units,
                price_units=10_000_000,
                amount_units=amount_units,
                commission_units=0,
                stamp_tax_units=0,
                other_fee_units=0,
                cash_balance_units=None,
                cash_balance_available=False,
                category="buy" if side == "buy" else "sell",
                occurrence_idx=0,
                source="import",
                version=1,
                summary="证券买入" if side == "buy" else "证券卖出",
                line_no=line_no,
                dedupe_key=f"{SYMBOL}:{uuid.uuid4().hex[:10]}",
            )
        )
        await session.commit()
    return trade_id


async def _job_row(engine: AsyncEngine, job_id: str) -> dict[str, Any] | None:
    async with engine.begin() as conn:
        row = (
            (
                await conn.execute(
                    text(
                        "select status, stage, error_code, error, result, progress "
                        "from platform.job where id = :id"
                    ),
                    {"id": job_id},
                )
            )
            .mappings()
            .first()
        )
    return dict(row) if row is not None else None


async def _count_flags(engine: AsyncEngine, user_id: uuid.UUID) -> int:
    async with engine.begin() as conn:
        return int(
            await conn.scalar(
                text("select count(*) from review.trade_flag where user_id = :uid"),
                {"uid": user_id},
            )
            or 0
        )


async def _flag_hashes(engine: AsyncEngine, user_id: uuid.UUID) -> set[str]:
    async with engine.begin() as conn:
        rows = await conn.execute(
            text("select distinct flags_hash from review.trade_flag where user_id = :uid"),
            {"uid": user_id},
        )
        return {str(value) for value in rows.scalars()}


# ── happy + 幂等重放 ────────────────────────────────────────


async def test_api_097_accepts_replays_and_builds(
    rebuild_client: httpx.AsyncClient,
    account_factory: Any,
    owner_engine: AsyncEngine,
    settings: Any,
    frozen_clock: FrozenClock,
) -> None:
    acc = await account_factory()
    await _seed_trade(owner_engine, user_id=acc.user_id, trade_date=date(2026, 6, 10))
    key = _idem_key()

    first = await _post(
        rebuild_client,
        acc.headers,
        body={"scope": "months", "months": ["2026-06"]},
        key=key,
    )
    assert first.status_code == 202, first.text
    job_id = first.json()["job_id"]
    assert first.headers["Location"] == f"{API_PREFIX}/jobs/{job_id}"
    assert first.headers["Retry-After"] == "1"
    assert "X-Job-Reused" not in first.headers
    accepted = first.json()
    assert accepted == {
        "job_id": job_id,
        "type": "rebuild_trade_flags",
        "status": "pending",
        "links": {
            "self": f"{API_PREFIX}/jobs/{job_id}",
            "events": f"{API_PREFIX}/jobs/{job_id}/events",
        },
    }

    # 同 key 同 body → 重放首次响应（同一 job_id + Idempotency-Replayed）
    replay = await _post(
        rebuild_client,
        acc.headers,
        body={"scope": "months", "months": ["2026-06"]},
        key=key,
    )
    assert replay.status_code == 202, replay.text
    assert replay.json()["job_id"] == job_id
    assert replay.headers.get("Idempotency-Replayed") == "true"
    assert replay.headers["Location"] == f"{API_PREFIX}/jobs/{job_id}"

    # 同 key 异体 → 422 IDEMPOTENCY_KEY_REUSED
    conflict = await _post(
        rebuild_client,
        acc.headers,
        body={"scope": "months", "months": ["2026-05"]},
        key=key,
    )
    assert conflict.status_code == 422, conflict.text
    assert conflict.json()["code"] == ErrorCode.IDEMPOTENCY_KEY_REUSED.value

    await _drain_worker(settings, clock=frozen_clock, owner_engine=owner_engine)

    job = await _job_row(owner_engine, job_id)
    assert job is not None
    assert job["status"] == "succeeded"
    assert job["stage"] == "done"
    assert job["progress"] == 100
    result = job["result"]
    assert isinstance(result, dict)
    assert result["months"] == ["2026-06"]
    assert result["months_count"] == 1
    assert result["trades"] == 1
    assert result["rows"] > 0
    assert result["flags_hash"] == DEFAULT_FLAGS_HASH
    assert result["truncated"] is False
    assert await _count_flags(owner_engine, acc.user_id) == result["rows"]
    assert await _flag_hashes(owner_engine, acc.user_id) == {DEFAULT_FLAGS_HASH}


# ── 单飞 ──────────────────────────────────────────────────


async def test_api_097_single_flight_and_conflict(
    rebuild_client: httpx.AsyncClient,
    account_factory: Any,
    owner_engine: AsyncEngine,
) -> None:
    acc = await account_factory()
    await _seed_trade(owner_engine, user_id=acc.user_id, trade_date=date(2026, 6, 10))

    first = await _post(
        rebuild_client, acc.headers, body={"scope": "months", "months": ["2026-06"]}
    )
    second = await _post(
        rebuild_client, acc.headers, body={"scope": "months", "months": ["2026-06"]}
    )
    assert first.status_code == 202, first.text
    assert second.status_code == 202, second.text
    assert second.json()["job_id"] == first.json()["job_id"]
    assert second.headers.get("X-Job-Reused") == "true"

    # 同 scope 异参在途 → 409（不能静默复用跑错月份）
    different = await _post(
        rebuild_client, acc.headers, body={"scope": "months", "months": ["2026-05"]}
    )
    assert different.status_code == 409, different.text
    assert different.json()["code"] == ErrorCode.JOB_ALREADY_RUNNING.value


# ── 校验 ──────────────────────────────────────────────────


async def test_api_097_validation_errors(
    rebuild_client: httpx.AsyncClient, account_factory: Any
) -> None:
    acc = await account_factory()

    missing = await rebuild_client.post(_REBUILD_PATH, headers=acc.headers)
    assert missing.status_code == 422, missing.text
    assert missing.json()["code"] == ErrorCode.VALIDATION_ERROR.value

    short = await rebuild_client.post(
        _REBUILD_PATH, headers={**acc.headers, "Idempotency-Key": "short"}
    )
    assert short.status_code == 422
    assert short.json()["code"] == ErrorCode.VALIDATION_ERROR.value

    all_with_months = await _post(
        rebuild_client, acc.headers, body={"scope": "all", "months": ["2026-06"]}
    )
    assert all_with_months.status_code == 422, all_with_months.text
    assert all_with_months.json()["code"] == ErrorCode.VALIDATION_ERROR.value

    months_empty = await _post(rebuild_client, acc.headers, body={"scope": "months"})
    assert months_empty.status_code == 422, months_empty.text

    for bad in ("2026-13", "2026-6", "26-06", "2026-06-01"):
        resp = await _post(rebuild_client, acc.headers, body={"scope": "months", "months": [bad]})
        assert resp.status_code == 422, f"{bad} → {resp.status_code}"
        assert resp.json()["code"] == ErrorCode.VALIDATION_ERROR.value

    overflow = [f"{2026 + index // 12:04d}-{index % 12 + 1:02d}" for index in range(121)]
    too_many = await _post(
        rebuild_client, acc.headers, body={"scope": "months", "months": overflow}
    )
    assert too_many.status_code == 422, too_many.text
    assert too_many.json()["code"] == ErrorCode.VALIDATION_ERROR.value


# ── scope=all：首末交易月逐月重建 ────────────────────────────


async def test_api_097_scope_all_covers_trade_month_range(
    rebuild_client: httpx.AsyncClient,
    account_factory: Any,
    owner_engine: AsyncEngine,
    settings: Any,
    frozen_clock: FrozenClock,
) -> None:
    acc = await account_factory()
    await _seed_trade(owner_engine, user_id=acc.user_id, trade_date=date(2026, 5, 8))
    await _seed_trade(owner_engine, user_id=acc.user_id, trade_date=date(2026, 6, 10), line_no=2)

    resp = await _post(rebuild_client, acc.headers, body={"scope": "all"})
    assert resp.status_code == 202, resp.text
    job_id = resp.json()["job_id"]
    await _drain_worker(settings, clock=frozen_clock, owner_engine=owner_engine)

    job = await _job_row(owner_engine, job_id)
    assert job is not None and isinstance(job["result"], dict)
    assert job["status"] == "succeeded"
    result = job["result"]
    assert result["months"] == ["2026-05", "2026-06"]
    assert result["months_count"] == 2
    assert result["trades"] == 2
    assert result["truncated"] is False
    assert await _count_flags(owner_engine, acc.user_id) == result["rows"]

    # 空账户：无交易不是失败，显式成功 + 空范围
    empty = await account_factory()
    empty_resp = await _post(rebuild_client, empty.headers, body={"scope": "all"})
    assert empty_resp.status_code == 202
    await _drain_worker(settings, clock=frozen_clock, owner_engine=owner_engine)
    empty_job = await _job_row(owner_engine, empty_resp.json()["job_id"])
    assert empty_job is not None and isinstance(empty_job["result"], dict)
    assert empty_job["status"] == "succeeded"
    assert empty_job["result"]["months"] == []
    assert empty_job["result"]["trades"] == 0


# ── 偏好联动：stale → 重建 → 清空 ────────────────────────────


async def test_api_097_rebuild_clears_stale_months(
    rebuild_client: httpx.AsyncClient,
    account_factory: Any,
    owner_engine: AsyncEngine,
    settings: Any,
    frozen_clock: FrozenClock,
) -> None:
    acc = await account_factory()
    await _seed_trade(owner_engine, user_id=acc.user_id, trade_date=date(2026, 6, 10))

    first = await _post(
        rebuild_client, acc.headers, body={"scope": "months", "months": ["2026-06"]}
    )
    assert first.status_code == 202, first.text
    await _drain_worker(settings, clock=frozen_clock, owner_engine=owner_engine)

    dashboard = await rebuild_client.get(f"{API_PREFIX}/dashboard/trade-flags", headers=acc.headers)
    assert dashboard.status_code == 200, dashboard.text
    assert dashboard.json()["stale_months"] == []
    assert dashboard.json()["flag_config"]["is_default"] is True

    patched = await rebuild_client.patch(
        f"{API_PREFIX}/settings",
        json={"bill_flags": {"near_equal_tolerance_pct": "2.50%", "window_trading_days": "3"}},
        headers={**acc.headers, "If-Match": 'W/"1"'},
    )
    assert patched.status_code == 200, patched.text
    changed_hash = patched.json()["bill_flags"]["flags_hash"]
    assert changed_hash != DEFAULT_FLAGS_HASH

    stale = await rebuild_client.get(f"{API_PREFIX}/dashboard/trade-flags", headers=acc.headers)
    assert stale.status_code == 200
    assert stale.json()["stale_months"] == ["2026-06"]
    assert stale.json()["flag_config"]["is_default"] is False

    again = await _post(rebuild_client, acc.headers, body={"scope": "all"})
    assert again.status_code == 202, again.text
    await _drain_worker(settings, clock=frozen_clock, owner_engine=owner_engine)

    cleared = await rebuild_client.get(f"{API_PREFIX}/dashboard/trade-flags", headers=acc.headers)
    assert cleared.status_code == 200
    assert cleared.json()["stale_months"] == []
    assert cleared.json()["flag_config"]["flags_hash"] == changed_hash
    assert await _flag_hashes(owner_engine, acc.user_id) == {changed_hash}


# ── 越权与隔离 ────────────────────────────────────────────


async def test_api_097_authz_and_cross_user_isolation(
    rebuild_client: httpx.AsyncClient,
    client: httpx.AsyncClient,
    account_factory: Any,
    owner_engine: AsyncEngine,
    settings: Any,
    frozen_clock: FrozenClock,
) -> None:
    unauth = await rebuild_client.post(
        _REBUILD_PATH,
        headers={"Idempotency-Key": _idem_key()},
        json={"scope": "all"},
    )
    assert unauth.status_code == 401
    assert unauth.json()["code"] == ErrorCode.TOKEN_EXPIRED.value

    user = await account_factory()
    token_resp = await client.post(
        f"{API_PREFIX}/tokens",
        headers=user.headers,
        json={"name": "read-only", "scopes": ["read"]},
    )
    assert token_resp.status_code == 201, token_resp.text
    pat = token_resp.json()["token"]["token"]

    read_only = await rebuild_client.post(
        _REBUILD_PATH,
        headers={"Authorization": f"Bearer {pat}", "Idempotency-Key": _idem_key()},
        json={"scope": "all"},
    )
    assert read_only.status_code == 403, read_only.text
    assert read_only.json()["code"] == ErrorCode.INSUFFICIENT_SCOPE.value

    alice = await account_factory()
    bob = await account_factory()
    await _seed_trade(owner_engine, user_id=alice.user_id, trade_date=date(2026, 6, 10))
    await _seed_trade(owner_engine, user_id=bob.user_id, trade_date=date(2026, 6, 11))

    alice_resp = await _post(rebuild_client, alice.headers, body={"scope": "all"})
    assert alice_resp.status_code == 202, alice_resp.text
    await _drain_worker(settings, clock=frozen_clock, owner_engine=owner_engine)

    assert await _count_flags(owner_engine, alice.user_id) > 0
    assert await _count_flags(owner_engine, bob.user_id) == 0
