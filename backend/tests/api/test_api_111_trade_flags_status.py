"""API-111 账单旗标重建状态 + API-097 `mode` 语义。

覆盖：
* 有账单无当前口径旗标 → `NO_CURRENT_FLAGS`，`mode=auto` 全时段重建；
* 待算旗标的未来行情未覆盖 → 不需重建；行情覆盖后 → `PENDING_MARKET_COVERED`，
  `mode=auto` 只重建覆盖月份，重建后无需再更新（再次 auto 空跑 `no_op=true`）；
* 旗标统计：按代码聚合的行数 / 种类数 / 总数与库内一致；
* 越权：无 token 401、admin 403；跨用户隔离（B 看不到 A 的旗标数）；
* 校验：`mode=auto` + `scope=months` → 422。
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime, timedelta
from typing import Any

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession
from stock_api.app import create_app
from stock_contracts.clock import FrozenClock
from stock_ledger.models import Trade
from stock_platform.ports import Bar, MarketCapabilities, Quote
from stock_worker.runner import WorkerRunner

pytestmark = pytest.mark.api

API_PREFIX = "/api/v1"
_STATUS_PATH = f"{API_PREFIX}/settings/trade-flags/status"
_REBUILD_PATH = f"{API_PREFIX}/settings/trade-flags/rebuild"
_CLOCK_TEXT = "2026-07-15T09:00:00+08:00"
SYMBOL = "600000"
BENCH = "hs300_tr"
CALENDAR = tuple(date(2026, 4, 1) + timedelta(days=index) for index in range(120))


class _ShiftingMarket:
    """可控 as_of 的回放行情桩：bars 只到 as_of（用于制造/解除 pending）。"""

    capabilities = MarketCapabilities(mode="replay", symbols=(SYMBOL, BENCH))
    provenance = "test"

    def __init__(self, as_of: date) -> None:
        self._as_of = as_of

    @property
    def as_of(self) -> date:
        return self._as_of

    def advance(self, as_of: date) -> None:
        self._as_of = as_of

    async def get_daily_bars(self, symbols: Sequence[str], start: date, end: date) -> Sequence[Bar]:
        stop = min(end, self._as_of)
        return [
            Bar(symbol=symbol, trade_date=day, close_units=10_000_000, return_units=0)
            for symbol in symbols
            for day in CALENDAR
            if start <= day <= stop
        ]

    async def get_latest_quotes(self, symbols: Sequence[str]) -> Sequence[Quote]:
        return ()


@pytest.fixture
def frozen_clock() -> FrozenClock:
    return FrozenClock(_CLOCK_TEXT)


@asynccontextmanager
async def _client(settings: Any, market: _ShiftingMarket) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(settings, clock=FrozenClock(_CLOCK_TEXT))
    app.state.sa.market_data = market  # type: ignore[assignment]
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client


async def _drain_worker(settings: Any, market: _ShiftingMarket, owner_engine: AsyncEngine) -> None:
    async with owner_engine.begin() as conn:
        await conn.execute(
            text("update platform.job set available_at = :ts where status = 'pending'"),
            {"ts": datetime(2000, 1, 1, tzinfo=UTC)},
        )
    runner = WorkerRunner(settings, clock=FrozenClock(_CLOCK_TEXT))
    runner._market_data = market
    try:
        while True:
            claimed = await runner._claim_one()
            if claimed is None:
                break
            await runner._execute(claimed)
    finally:
        await runner.close()


async def _seed_trade(engine: AsyncEngine, *, user_id: uuid.UUID, trade_date: date) -> None:
    async with AsyncSession(engine) as session:
        session.add(
            Trade(
                id=f"trd_{uuid.uuid4().hex[:12]}",
                user_id=user_id,
                batch_id=None,
                trade_date=trade_date,
                security_code=SYMBOL,
                security_name="测试股",
                side="buy",
                quantity_units=1000,
                price_units=10_000_000,
                amount_units=100,
                commission_units=0,
                stamp_tax_units=0,
                other_fee_units=0,
                cash_balance_units=None,
                cash_balance_available=False,
                category="buy",
                occurrence_idx=0,
                source="import",
                version=1,
                summary="证券买入",
                line_no=1,
                dedupe_key=f"{SYMBOL}:{uuid.uuid4().hex[:10]}",
            )
        )
        await session.commit()


async def _post_rebuild(
    client: httpx.AsyncClient,
    headers: dict[str, str],
    *,
    body: dict[str, Any],
) -> httpx.Response:
    return await client.post(
        _REBUILD_PATH,
        headers={**headers, "Idempotency-Key": f"tfr-{uuid.uuid4().hex}"},
        json=body,
    )


async def _job_result(engine: AsyncEngine, job_id: str) -> dict[str, Any]:
    async with engine.begin() as conn:
        result = await conn.scalar(
            text("select result from platform.job where id = :id"), {"id": job_id}
        )
    assert isinstance(result, dict)
    return result


async def _get_status(client: httpx.AsyncClient, headers: dict[str, str]) -> dict[str, Any]:
    resp = await client.get(_STATUS_PATH, headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert isinstance(body, dict)
    return body


async def test_api_111_missing_current_flags_then_auto_full_rebuild(
    settings: Any,
    owner_engine: AsyncEngine,
    account_factory: Any,
) -> None:
    market = _ShiftingMarket(date(2026, 6, 20))
    acc = await account_factory()
    await _seed_trade(owner_engine, user_id=acc.user_id, trade_date=date(2026, 6, 10))

    async with _client(settings, market) as client:
        status = await _get_status(client, acc.headers)
        assert status["needs_rebuild"] is True
        assert status["has_bill_data"] is True
        assert status["bill_count"] == 1
        assert status["current_flag_rows"] == 0
        assert status["flag_kinds"] == 0
        assert status["flag_total"] == 0
        assert [item["code"] for item in status["reasons"]] == ["NO_CURRENT_FLAGS"]

        accepted = await _post_rebuild(client, acc.headers, body={"scope": "all", "mode": "auto"})
        assert accepted.status_code == 202, accepted.text
        job_id = accepted.json()["job_id"]

        await _drain_worker(settings, market, owner_engine)
        result = await _job_result(owner_engine, job_id)
        assert result["mode"] == "auto"
        assert result["months"] == ["2026-06"]
        assert result["rows"] == 8  # 买入侧 8 个旗标各一行

        after = await _get_status(client, acc.headers)
        assert after["needs_rebuild"] is False
        assert after["current_flag_rows"] == 8
        assert after["flag_kinds"] == 8
        assert after["flag_total"] == 8
        assert after["pending_count"] == 0
        assert [item["count"] for item in after["flags"]] == [1] * 8


async def test_api_111_pending_then_auto_incremental(
    settings: Any,
    owner_engine: AsyncEngine,
    account_factory: Any,
) -> None:
    market = _ShiftingMarket(date(2026, 6, 11))
    acc = await account_factory()
    await _seed_trade(owner_engine, user_id=acc.user_id, trade_date=date(2026, 6, 10))

    async with _client(settings, market) as client:
        # 窗口只有 1 个交易日 → 2 个事后旗标（短期抄底/短期被套）为 pending。
        accepted = await _post_rebuild(client, acc.headers, body={"scope": "all", "mode": "full"})
        assert accepted.status_code == 202, accepted.text
        await _drain_worker(settings, market, owner_engine)

        early = await _get_status(client, acc.headers)
        assert early["pending_count"] == 2
        assert early["pending_covered_count"] == 0
        assert early["needs_rebuild"] is False

        market.advance(date(2026, 6, 12))
        still_pending = await _get_status(client, acc.headers)
        assert still_pending["pending_covered_count"] == 0
        assert still_pending["needs_rebuild"] is False

        market.advance(date(2026, 6, 20))
        covered = await _get_status(client, acc.headers)
        assert covered["pending_count"] == 2
        assert covered["pending_covered_count"] == 2
        assert covered["pending_months"] == ["2026-06"]
        assert covered["needs_rebuild"] is True
        assert [item["code"] for item in covered["reasons"]] == ["PENDING_MARKET_COVERED"]

        # auto 只重建行情已覆盖的 2026-06；重建后 pending 清零。
        accepted = await _post_rebuild(client, acc.headers, body={"scope": "all", "mode": "auto"})
        assert accepted.status_code == 202, accepted.text
        job_id = accepted.json()["job_id"]
        await _drain_worker(settings, market, owner_engine)
        result = await _job_result(owner_engine, job_id)
        assert result["mode"] == "auto"
        assert result["months"] == ["2026-06"]

        settled = await _get_status(client, acc.headers)
        assert settled["pending_count"] == 0
        assert settled["needs_rebuild"] is False

        # 再点一次 auto：无待算 → 空跑成功。
        accepted = await _post_rebuild(client, acc.headers, body={"scope": "all", "mode": "auto"})
        job_id = accepted.json()["job_id"]
        await _drain_worker(settings, market, owner_engine)
        no_op = await _job_result(owner_engine, job_id)
        assert no_op["no_op"] is True
        assert no_op["months"] == []


async def test_api_111_auth_and_isolation(
    settings: Any,
    owner_engine: AsyncEngine,
    account_factory: Any,
) -> None:
    market = _ShiftingMarket(date(2026, 6, 20))
    owner = await account_factory()
    other = await account_factory()
    await _seed_trade(owner_engine, user_id=owner.user_id, trade_date=date(2026, 6, 10))
    await _seed_trade(owner_engine, user_id=other.user_id, trade_date=date(2026, 6, 11))

    async with _client(settings, market) as client:
        unauth = await client.get(_STATUS_PATH)
        assert unauth.status_code == 401

        admin = await account_factory(role="admin")
        forbidden = await client.get(_STATUS_PATH, headers=admin.headers)
        assert forbidden.status_code == 403
        assert forbidden.json()["code"] == "ADMIN_CANNOT_READ_TRADES"

        owner_status = await _get_status(client, owner.headers)
        other_status = await _get_status(client, other.headers)
        assert owner_status["bill_count"] == 1
        assert other_status["bill_count"] == 1
        assert owner_status["current_flag_rows"] == other_status["current_flag_rows"] == 0

        invalid = await _post_rebuild(
            client,
            owner.headers,
            body={"scope": "months", "months": ["2026-06"], "mode": "auto"},
        )
        assert invalid.status_code == 422, invalid.text
        assert invalid.json()["code"] == "VALIDATION_ERROR"


async def test_api_111_incomplete_coverage_then_auto_completes(
    settings: Any,
    owner_engine: AsyncEngine,
    account_factory: Any,
) -> None:
    """已有部分旗标、新增账单未构建 → INCOMPLETE_COVERAGE；auto 只补建未覆盖月份。"""
    # 行情推进到 7/25，足以覆盖 6/10 与 7/15 的事后窗口（避免 pending 干扰本用例）。
    market = _ShiftingMarket(date(2026, 7, 25))
    acc = await account_factory()
    await _seed_trade(owner_engine, user_id=acc.user_id, trade_date=date(2026, 6, 10))
    await _seed_trade(owner_engine, user_id=acc.user_id, trade_date=date(2026, 7, 15))

    async with _client(settings, market) as client:
        # 只构建 2026-06：7 月账单没有旗标 → 未完全覆盖。
        accepted = await _post_rebuild(
            client, acc.headers, body={"scope": "months", "months": ["2026-06"], "mode": "full"}
        )
        assert accepted.status_code == 202, accepted.text
        await _drain_worker(settings, market, owner_engine)

        status = await _get_status(client, acc.headers)
        assert status["current_flag_rows"] == 8  # 仅 6 月
        assert status["pending_count"] == 0
        assert status["uncovered_count"] == 1
        assert status["uncovered_months"] == ["2026-07"]
        assert status["needs_rebuild"] is True
        codes = [item["code"] for item in status["reasons"]]
        assert "INCOMPLETE_COVERAGE" in codes
        assert "NO_CURRENT_FLAGS" not in codes

        # auto：版本未变、无 pending → 只合并未覆盖月份 2026-07。
        accepted = await _post_rebuild(client, acc.headers, body={"scope": "all", "mode": "auto"})
        assert accepted.status_code == 202, accepted.text
        job_id = accepted.json()["job_id"]
        await _drain_worker(settings, market, owner_engine)
        result = await _job_result(owner_engine, job_id)
        assert result["mode"] == "auto"
        assert result["months"] == ["2026-07"]
        assert result["rows"] == 8

        after = await _get_status(client, acc.headers)
        assert after["uncovered_count"] == 0
        assert after["uncovered_months"] == []
        assert after["current_flag_rows"] == 16
        assert after["needs_rebuild"] is False

        # 再点一次 auto：无 pending、无未覆盖 → 空跑。
        accepted = await _post_rebuild(client, acc.headers, body={"scope": "all", "mode": "auto"})
        third_job_id = accepted.json()["job_id"]
        await _drain_worker(settings, market, owner_engine)
        no_op = await _job_result(owner_engine, third_job_id)
        assert no_op["no_op"] is True
        assert no_op["uncovered_count"] == 0
        assert no_op["months"] == []


async def test_api_111_corporate_action_not_counted_as_uncovered(
    settings: Any,
    owner_engine: AsyncEngine,
    account_factory: Any,
) -> None:
    """公司行动流水不产生旗标，不应被判定为未覆盖。"""
    market = _ShiftingMarket(date(2026, 7, 25))
    acc = await account_factory()
    await _seed_trade(owner_engine, user_id=acc.user_id, trade_date=date(2026, 6, 10))
    async with _client(settings, market) as client:
        # 先全量构建 6 月。
        await _post_rebuild(client, acc.headers, body={"scope": "all", "mode": "full"})
        await _drain_worker(settings, market, owner_engine)

        # 插入一笔公司行动流水（分红/送股类：amount=0、quantity>0、category 标记）。
        async with AsyncSession(owner_engine) as session:
            session.add(
                Trade(
                    id=f"trd_{uuid.uuid4().hex[:12]}",
                    user_id=acc.user_id,
                    batch_id=None,
                    trade_date=date(2026, 7, 15),
                    security_code=SYMBOL,
                    security_name="测试股",
                    side="buy",
                    quantity_units=100,
                    price_units=0,
                    amount_units=0,
                    commission_units=0,
                    stamp_tax_units=0,
                    other_fee_units=0,
                    cash_balance_units=None,
                    cash_balance_available=False,
                    category="dividend",
                    occurrence_idx=0,
                    source="import",
                    version=1,
                    summary="红利入账",
                    line_no=1,
                    dedupe_key=f"{SYMBOL}:{uuid.uuid4().hex[:10]}",
                )
            )
            await session.commit()

        status = await _get_status(client, acc.headers)
        # 公司行动流水不计入未覆盖：uncovered 仍为 0。
        assert status["bill_count"] == 2
        assert status["uncovered_count"] == 0
        assert status["needs_rebuild"] is False
