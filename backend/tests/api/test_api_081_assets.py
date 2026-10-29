"""API-081 验收：`GET /assets`（每日资产快照列表 + 构建摘要）。

覆盖设计 §9.3（API-081 AT-01~AT-10 / AT-20~AT-24）：
* happy path：日期倒序、06-30 行金额与手算 oracle 一致、`build.state=current`；
* 未构建 → 空列表 + `build.state=missing`（owner 直查无行）；
* 跨用户隔离（0 条泄露）、admin 403、无 token 401；
* blocked 行数值字段**显式 null** + `status_reason` 非空；
* 参数校验：limit 边界 / from>to / status 非法 / sort 白名单 / 未知 query / 损坏游标；
* from/to 闭区间、sort 升序与默认倒序互逆；
* stale → 重建后 current（时钟跨日驱动增量）。

时钟口径：`GET /assets` 的 `build.state` 依赖 `clock.today()`，因此本文件用
`create_app(settings, clock=FrozenClock(...))` 装配一个**确定性 app**，并用同一
`FrozenClock` 驱动 `WorkerRunner`（`_drain_worker`），保证 API 与 worker 的“今天”一致。
"""

from __future__ import annotations

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

_MONEY_RE = re.compile(r"^-?\d+\.\d{2}$")
_RATIO_RE = re.compile(r"^-?\d+\.\d{8}$")
_PCT_RE = re.compile(r"^-?\d+\.\d{2}%$")

_BUILD_CLOCK = "2026-07-01T09:00:00+00:00"
# 数据截止日（fixtures/market/quotes.json 的 as_of，也是 build.data_as_of 血缘）。
_MARKET_AS_OF = "2026-09-30"

# 手算 oracle（设计附录 C / UT-02）：
#   05-29 余额行 100000.00（期初现金锚点）
#   06-30 买 100 股 @1700.00（-170000.00，余额 -70000.00）
#   06-30 收盘 1706.20 → 市值 170620.00，总资产 100620.00，pnl 620.00，日收益 0.62%
_ORACLE_0630 = {
    "cash": "-70000.00",
    "money_fund": "0.00",
    "balance": "-70000.00",
    "market_value": "170620.00",
    "total_assets": "100620.00",
    "holding_cost": "170000.00",
    "unrealized_pnl": "620.00",
    "holding_count": 1,
    "cashflow": "0.00",
    "prev_total_assets": "100000.00",
    "daily_pnl": "620.00",
    "daily_return": "0.00620000",
    "daily_return_pct": "0.62%",
}


# ── 确定性 app（FrozenClock）+ worker ────────────────────────


@pytest.fixture
def frozen_clock() -> FrozenClock:
    return FrozenClock(_BUILD_CLOCK)


@pytest_asyncio.fixture
async def assets_client(settings: Any, frozen_clock: FrozenClock) -> Any:
    """与 worker 同钟的确定性 ASGI 客户端（不跑 lifespan：schema 由迁移保证）。"""
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
    """把队列里的任务全部执行完（build_assets 需要行情端口 → 走懒装配）。

    注意：`platform.job.available_at` 的默认值来自 DB `now()`（真实时钟），而本文件把
    app/worker 时钟固定在 2026-07-01；`claim` 的条件是 `available_at <= clock.now()`，
    因此测试侧必须先把 pending 任务的 `available_at` 拨到过去，才能模拟“执行时刻已到”。
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
    return f"assets-{uuid.uuid4().hex}"  # 32 位，满足 16~128


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


async def _build_and_drain(
    client: httpx.AsyncClient,
    settings: Any,
    headers: dict[str, str],
    clock: FrozenClock,
    owner_engine: AsyncEngine,
    *,
    mode: str | None = None,
) -> None:
    resp = await _post_build(client, headers, mode=mode)
    assert resp.status_code == 202, resp.text
    await _drain_worker(settings, clock=clock, owner_engine=owner_engine)


# ── 造数（owner 身份，绕过 RLS）──────────────────────────────


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
    """设计附录 C 的 oracle 场景：期初余额 + 月末买入（600519 月末收盘 1706.20）。"""
    await _seed_trade(
        engine,
        user_id=user_id,
        trade_date=date(2026, 5, 29),
        side="buy",
        cash_balance_units=100_000_00,
        category="other",
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


async def _seed_missing_market(engine: AsyncEngine, *, user_id: uuid.UUID) -> None:
    """缺行情阻断：持有夹具里不存在的标的 999999 → 受影响日 blocked。"""
    await _seed_trade(
        engine,
        user_id=user_id,
        trade_date=date(2026, 5, 29),
        side="buy",
        cash_balance_units=100_000_00,
        category="other",
    )
    await _seed_trade(
        engine,
        user_id=user_id,
        trade_date=date(2026, 6, 1),
        side="buy",
        quantity_units=100_0000,
        price_units=1000_00,
        amount_units=-10_000_00,
        cash_balance_units=90_000_00,
        category="buy",
        security_code="999999",
    )


async def _seed_no_anchor(engine: AsyncEngine, *, user_id: uuid.UUID) -> None:
    """全部 cash_balance 为 NULL → 全局 blocked（不写日行）。"""
    await _seed_trade(
        engine,
        user_id=user_id,
        trade_date=date(2026, 6, 1),
        side="buy",
        quantity_units=100_0000,
        price_units=1700_000000,
        amount_units=-170_000_00,
        category="buy",
    )


async def _count_rows(engine: AsyncEngine, *, user_id: uuid.UUID) -> int:
    async with engine.begin() as conn:
        return int(
            await conn.scalar(
                text("select count(*) from review.asset_daily where user_id = :uid"),
                {"uid": user_id},
            )
            or 0
        )


# ── AT-01 happy path ────────────────────────────────────────


async def test_api_081_happy_path_oracle(
    assets_client: Any,
    account_factory: Any,
    owner_engine: AsyncEngine,
    settings: Any,
    frozen_clock: FrozenClock,
) -> None:
    """200；日期倒序；06-30 行与 oracle 一致；build.state=current。"""
    acc = await account_factory()
    await _seed_happy_chain(owner_engine, user_id=acc.user_id)
    await _build_and_drain(assets_client, settings, acc.headers, frozen_clock, owner_engine)

    resp = await assets_client.get("/api/v1/assets", headers=acc.headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()

    items = body["items"]
    assert items, "构建后应至少一行"
    dates = [item["date"] for item in items]
    assert dates == sorted(dates, reverse=True), "默认应按 trade_date 倒序"
    assert body["definition_version"] == "1.2"
    assert body["data_as_of"] == _MARKET_AS_OF

    row = next(item for item in items if item["date"] == "2026-06-30")
    for field, expected in _ORACLE_0630.items():
        assert row[field] == expected, (field, row[field], expected)
    assert row["status"] == "ok"
    assert row["status_reason"] is None

    # 金额/比率格式
    for field in (
        "cash",
        "money_fund",
        "balance",
        "market_value",
        "total_assets",
        "holding_cost",
        "unrealized_pnl",
        "cashflow",
        "prev_total_assets",
        "daily_pnl",
    ):
        assert _MONEY_RE.match(row[field]), (field, row[field])
    assert _RATIO_RE.match(row["daily_return"])
    assert _PCT_RE.match(row["daily_return_pct"])
    assert row["daily_return_pct"] == "0.62%"

    build = body["build"]
    assert build["state"] == "current"
    assert build["last_status"] == "ok"
    assert build["last_mode"] == "full"
    assert build["last_built_through"] == "2026-07-01"
    assert build["last_built_at"] is not None
    assert build["assumption"] == "back_solved"
    assert build["has_new_market_data"] is False
    # 2026-05-29 .. 2026-07-01 每个自然日一行
    expected_days = (date(2026, 7, 1) - date(2026, 5, 29)).days + 1
    assert build["rows_count"] == expected_days
    assert len(items) == expected_days
    assert build["last_rows_written"] == expected_days
    assert build["definition_version"] == "1.2"


# ── AT-02 未构建 / AT-10 全局 blocked ───────────────────────


async def test_api_081_not_built_returns_missing(
    assets_client: Any, account_factory: Any, owner_engine: AsyncEngine
) -> None:
    """未构建 → 200 + items=[] + build.state=missing；owner 直查无行。"""
    acc = await account_factory()
    resp = await assets_client.get("/api/v1/assets", headers=acc.headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["items"] == []
    build = body["build"]
    assert build["state"] == "missing"
    assert build["rows_count"] == 0
    assert build["last_mode"] is None
    assert build["last_built_through"] is None
    assert build["has_new_market_data"] is False
    assert body["definition_version"] == "1.2"
    assert body["data_as_of"] is None
    assert await _count_rows(owner_engine, user_id=acc.user_id) == 0


async def test_api_081_global_blocked(
    assets_client: Any,
    account_factory: Any,
    owner_engine: AsyncEngine,
    settings: Any,
    frozen_clock: FrozenClock,
) -> None:
    """无现金锚点 → 200 + 空 items + build.state=blocked + status_reason 非空。"""
    acc = await account_factory()
    await _seed_no_anchor(owner_engine, user_id=acc.user_id)
    await _build_and_drain(assets_client, settings, acc.headers, frozen_clock, owner_engine)

    resp = await assets_client.get("/api/v1/assets", headers=acc.headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["items"] == []
    build = body["build"]
    assert build["state"] == "blocked"
    assert build["last_status"] == "blocked"
    assert build["last_built_through"] is None
    assert build["rows_count"] == 0
    assert isinstance(build["status_reason"], str) and build["status_reason"]


# ── AT-03 跨用户隔离 / AT-04 admin / AT-05 未认证 ───────────


async def test_api_081_cross_user_isolation(
    assets_client: Any,
    account_factory: Any,
    owner_engine: AsyncEngine,
    settings: Any,
    frozen_clock: FrozenClock,
) -> None:
    owner = await account_factory()
    intruder = await account_factory()
    await _seed_happy_chain(owner_engine, user_id=owner.user_id)
    await _build_and_drain(assets_client, settings, owner.headers, frozen_clock, owner_engine)

    intruder_resp = await assets_client.get("/api/v1/assets", headers=intruder.headers)
    assert intruder_resp.status_code == 200, intruder_resp.text
    assert intruder_resp.json()["items"] == [], "B 的列表看到了 A 的资产行"

    owner_resp = await assets_client.get("/api/v1/assets", headers=owner.headers)
    assert owner_resp.status_code == 200
    owner_dates = {item["date"] for item in owner_resp.json()["items"]}
    assert "2026-06-30" in owner_dates, "A 自己的资产行应可见（防止假阳性）"


async def test_api_081_admin_forbidden(assets_client: Any, account_factory: Any) -> None:
    admin = await account_factory(role="admin")
    resp = await assets_client.get("/api/v1/assets", headers=admin.headers)
    assert resp.status_code == 403, resp.text
    assert resp.json()["code"] == ErrorCode.ADMIN_CANNOT_READ_TRADES.value


async def test_api_081_unauthenticated_401(assets_client: Any) -> None:
    resp = await assets_client.get("/api/v1/assets")
    assert resp.status_code == 401, resp.text
    assert resp.json()["code"] == ErrorCode.TOKEN_EXPIRED.value


# ── AT-08 blocked 行数值显式 null ──────────────────────────


async def test_api_081_blocked_rows_are_null(
    assets_client: Any,
    account_factory: Any,
    owner_engine: AsyncEngine,
    settings: Any,
    frozen_clock: FrozenClock,
) -> None:
    acc = await account_factory()
    await _seed_missing_market(owner_engine, user_id=acc.user_id)
    await _build_and_drain(assets_client, settings, acc.headers, frozen_clock, owner_engine)

    resp = await assets_client.get(
        "/api/v1/assets", params={"status": "blocked"}, headers=acc.headers
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["items"], "持有无行情标的的日期应出现 blocked 行"
    for item in body["items"]:
        assert item["status"] == "blocked"
        for field in (
            "cash",
            "money_fund",
            "balance",
            "market_value",
            "total_assets",
            "holding_cost",
            "unrealized_pnl",
            "cashflow",
            "prev_total_assets",
            "daily_pnl",
            "daily_return",
            "daily_return_pct",
        ):
            assert item[field] is None, (field, item[field])
        assert item["holding_count"] == 0
        assert isinstance(item["status_reason"], str) and item["status_reason"]
    # 逐日 blocked 使整体为 degraded（不是 blocked；blocked 仅用于全局无锚点）。
    assert body["build"]["last_status"] == "degraded"


# ── AT-06/07 游标与参数校验 ────────────────────────────────


async def test_api_081_query_validation(
    assets_client: Any,
    account_factory: Any,
    owner_engine: AsyncEngine,
    settings: Any,
    frozen_clock: FrozenClock,
) -> None:
    acc = await account_factory()
    await _seed_happy_chain(owner_engine, user_id=acc.user_id)
    await _build_and_drain(assets_client, settings, acc.headers, frozen_clock, owner_engine)
    headers = acc.headers

    invalid_period = await assets_client.get(
        "/api/v1/assets", params={"from": "2026-07-01", "to": "2026-06-01"}, headers=headers
    )
    assert invalid_period.status_code == 422
    assert invalid_period.json()["code"] == ErrorCode.INVALID_PERIOD.value

    bad_status = await assets_client.get(
        "/api/v1/assets", params={"status": "foo"}, headers=headers
    )
    assert bad_status.status_code == 422
    assert bad_status.json()["code"] == ErrorCode.VALIDATION_ERROR.value

    bad_sort = await assets_client.get("/api/v1/assets", params={"sort": "amount"}, headers=headers)
    assert bad_sort.status_code == 400
    assert bad_sort.json()["code"] == ErrorCode.INVALID_SORT_FIELD.value

    unknown_query = await assets_client.get(
        "/api/v1/assets", params={"bogus": "1"}, headers=headers
    )
    assert unknown_query.status_code == 400
    assert unknown_query.json()["code"] == ErrorCode.UNKNOWN_QUERY_PARAM.value

    bad_cursor = await assets_client.get(
        "/api/v1/assets", params={"cursor": "!!!not-a-cursor!!!"}, headers=headers
    )
    assert bad_cursor.status_code == 400
    assert bad_cursor.json()["code"] == ErrorCode.INVALID_CURSOR.value

    for bad_limit in (0, 201):
        resp = await assets_client.get(
            "/api/v1/assets", params={"limit": bad_limit}, headers=headers
        )
        assert resp.status_code == 422, (bad_limit, resp.text)


# ── AT-06/20/21/24 游标翻页、闭区间、排序、limit 边界 ────────


async def test_api_081_cursor_pagination_and_filters(
    assets_client: Any,
    account_factory: Any,
    owner_engine: AsyncEngine,
    settings: Any,
    frozen_clock: FrozenClock,
) -> None:
    acc = await account_factory()
    await _seed_happy_chain(owner_engine, user_id=acc.user_id)
    await _build_and_drain(assets_client, settings, acc.headers, frozen_clock, owner_engine)
    headers = acc.headers

    # 游标翻全集无重无漏
    collected: list[str] = []
    cursor: str | None = None
    while True:
        params: dict[str, Any] = {"limit": 10}
        if cursor is not None:
            params["cursor"] = cursor
        resp = await assets_client.get("/api/v1/assets", params=params, headers=headers)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        collected.extend(item["date"] for item in body["items"])
        if not body["has_more"]:
            assert body["next_cursor"] is None
            break
        cursor = body["next_cursor"]
        assert cursor is not None
    total = await _count_rows(owner_engine, user_id=acc.user_id)
    assert len(collected) == total
    assert len(set(collected)) == len(collected), "游标翻页出现重复"
    assert collected == sorted(collected, reverse=True)

    # limit 边界：1 可用 + has_more；200 可用
    one = await assets_client.get("/api/v1/assets", params={"limit": 1}, headers=headers)
    assert one.status_code == 200
    assert len(one.json()["items"]) == 1
    assert one.json()["has_more"] is True
    assert one.json()["next_cursor"] is not None
    two_hundred = await assets_client.get("/api/v1/assets", params={"limit": 200}, headers=headers)
    assert two_hundred.status_code == 200

    # from/to 闭区间含端点
    single = await assets_client.get(
        "/api/v1/assets",
        params={"from": "2026-06-30", "to": "2026-06-30"},
        headers=headers,
    )
    assert single.status_code == 200
    assert [item["date"] for item in single.json()["items"]] == ["2026-06-30"]

    # sort=trade_date 升序与默认倒序互逆（取全量比较，而非各自首页）
    asc = await assets_client.get(
        "/api/v1/assets", params={"sort": "trade_date", "limit": 200}, headers=headers
    )
    desc = await assets_client.get("/api/v1/assets", params={"limit": 200}, headers=headers)
    asc_dates = [item["date"] for item in asc.json()["items"]]
    desc_dates = [item["date"] for item in desc.json()["items"]]
    assert asc_dates == sorted(asc_dates)
    assert desc_dates == sorted(desc_dates, reverse=True)
    assert len(asc_dates) == total
    assert desc_dates == list(reversed(asc_dates))


# ── AT-09 stale → 重建后 current ───────────────────────────


async def test_api_081_stale_after_new_day_then_current(
    assets_client: Any,
    account_factory: Any,
    owner_engine: AsyncEngine,
    settings: Any,
    frozen_clock: FrozenClock,
) -> None:
    acc = await account_factory()
    await _seed_happy_chain(owner_engine, user_id=acc.user_id)
    await _build_and_drain(assets_client, settings, acc.headers, frozen_clock, owner_engine)

    current = await assets_client.get("/api/v1/assets", headers=acc.headers)
    assert current.json()["build"]["state"] == "current"

    # 时钟跨日 + 新流水 → stale（两个维度任一都足以 stale）
    frozen_clock.advance(days=1)
    await _seed_trade(
        owner_engine,
        user_id=acc.user_id,
        trade_date=date(2026, 7, 2),
        side="buy",
        cash_balance_units=-70_000_00,
        category="other",
    )
    stale = await assets_client.get("/api/v1/assets", headers=acc.headers)
    assert stale.status_code == 200
    assert stale.json()["build"]["state"] == "stale"

    await _build_and_drain(assets_client, settings, acc.headers, frozen_clock, owner_engine)
    refreshed = await assets_client.get("/api/v1/assets", headers=acc.headers)
    body = refreshed.json()
    assert body["build"]["state"] == "current"
    assert body["build"]["last_built_through"] == "2026-07-02"
    assert body["build"]["last_mode"] == "incremental"
    assert body["items"][0]["date"] == "2026-07-02"
