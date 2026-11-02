"""API-087 `GET /sectors/overview` 验收（设计 §7.1）。

覆盖：happy（行业 L1 / 概念）、无 token 401、admin 403、B 账号隔离、
422（未来 as_of / taxonomy / level / benchmark / limit）、400 UNKNOWN_QUERY_PARAM、
PIT fail-closed（as_of 早于首次同步日 → 200 blocked）、未构建 / 无股票持仓降级、
只读计数不变、ETag/304。
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
from stock_ledger.models import Trade
from stock_worker.runner import WorkerRunner

pytestmark = pytest.mark.api

_CLOCK = "2026-07-01T09:00:00+00:00"
_MONEY_RE = re.compile(r"^-?\d+\.\d{2}$")
_RATIO_RE = re.compile(r"^-?\d+\.\d{8}$")
_PCT_RE = re.compile(r"^-?\d+\.\d{2}%$")
_HEX16_RE = re.compile(r"^[0-9a-f]{16}$")
_HEX64_RE = re.compile(r"^[0-9a-f]{64}$")


@pytest.fixture
def frozen_clock() -> FrozenClock:
    return FrozenClock(_CLOCK)


@pytest_asyncio.fixture
async def sector_client(settings: Any, frozen_clock: FrozenClock) -> Any:
    app = create_app(settings, clock=frozen_clock)
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client


async def _seed_trade(
    engine: AsyncEngine,
    *,
    user_id: uuid.UUID,
    trade_date: date,
    quantity_units: int = 0,
    price_units: int = 0,
    amount_units: int = 0,
    cash_balance_units: int | None = None,
    category: str = "other",
    security_code: str = "600519",
) -> None:
    async with AsyncSession(engine) as session:
        session.add(
            Trade(
                id=f"trd_{uuid.uuid4().hex[:12]}",
                user_id=user_id,
                batch_id=None,
                trade_date=trade_date,
                security_code=security_code,
                security_name=security_code,
                side="buy" if category == "buy" else "other",
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
                summary=category,
                dedupe_key=f"{security_code}:{uuid.uuid4().hex[:10]}",
            )
        )
        await session.commit()


async def _drain_worker(settings: Any, *, clock: FrozenClock, owner_engine: AsyncEngine) -> None:
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


async def _build_assets(
    client: httpx.AsyncClient,
    settings: Any,
    headers: dict[str, str],
    clock: FrozenClock,
    owner_engine: AsyncEngine,
) -> None:
    resp = await client.post(
        "/api/v1/assets/build", headers={**headers, "Idempotency-Key": f"sector-{uuid.uuid4().hex}"}
    )
    assert resp.status_code == 202, resp.text
    await _drain_worker(settings, clock=clock, owner_engine=owner_engine)


async def _seed_stock_portfolio(
    client: httpx.AsyncClient,
    settings: Any,
    headers: dict[str, str],
    clock: FrozenClock,
    owner_engine: AsyncEngine,
    *,
    user_id: uuid.UUID,
    codes: tuple[str, ...] = ("600519",),
) -> None:
    """04-01 转入 100000 + 06-30 各买入 100 股（价格 1700，余额可为负）。

    只造股票持仓（用于板块映射）；现金锚点保证资产表可构建。
    """
    await _seed_trade(
        owner_engine,
        user_id=user_id,
        trade_date=date(2026, 4, 1),
        amount_units=100_000_00,
        cash_balance_units=100_000_00,
        category="transfer",
        security_code="",
    )
    balance = 100_000_00
    for code in codes:
        balance -= 170_000_00
        await _seed_trade(
            owner_engine,
            user_id=user_id,
            trade_date=date(2026, 6, 30),
            quantity_units=100_0000,
            price_units=1700_000000,
            amount_units=-170_000_00,
            cash_balance_units=balance,
            category="buy",
            security_code=code,
        )
    await _build_assets(client, settings, headers, clock, owner_engine)


async def _count(engine: AsyncEngine, sql: str) -> int:
    async with engine.begin() as conn:
        return int(await conn.scalar(text(sql)) or 0)


# ── happy ────────────────────────────────────────────────────


async def test_api_087_industry_happy(
    sector_client: Any,
    account_factory: Any,
    owner_engine: AsyncEngine,
    settings: Any,
    frozen_clock: FrozenClock,
) -> None:
    acc = await account_factory()
    await _seed_stock_portfolio(
        sector_client, settings, acc.headers, frozen_clock, owner_engine, user_id=acc.user_id
    )

    resp = await sector_client.get(
        "/api/v1/sectors/overview",
        headers=acc.headers,
        params={"taxonomy": "industry", "level": 1},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert body["as_of"] == "2026-07-01"
    assert body["taxonomy"] == "industry"
    assert body["level"] == 1
    assert body["status"] == "ok"
    assert body["asset_build_state"] == "current"
    assert body["sector_data_state"] == "current"
    assert body["definition_version"] == "1.0"
    assert body["sector_dataset_version"] == "sw2021@2026-06-30"
    assert body["data_as_of"] == "2026-07-01"
    assert body["sector_data_as_of"] == "2026-06-30"
    assert body["benchmark_id"] == "hs300"
    assert body["weight_mode"] == "additive"
    assert body["missing"] == []
    assert re.match(r"^[0-9a-f]{64}$", body["inputs_hash"])
    assert body["links"]["sectors"] == "/sectors?taxonomy=industry&level=1"

    portfolio = body["portfolio"]
    assert portfolio["coverage"] == "1.00000000"
    assert portfolio["coverage_pct"] == "100.00%"
    assert portfolio["coverage_basis"] == "mapped_equity_market_value / equity_market_value"
    assert portfolio["unclassified_market_value"] == "0.00"
    assert portfolio["non_stock_market_value"] == "0.00"
    assert portfolio["classified_holding_count"] == 1

    rows = body["sectors"]
    # 行业通用口径：持仓板块 ∪ 基准板块（非持仓基准板块以低配出现）
    assert len(rows) == 4
    by_code = {row["sector_code"]: row for row in rows}
    food = by_code["801120.SI"]
    assert food["sector_name"] == "食品饮料"
    assert food["level"] == 1
    assert food["weight"] == "1.00000000"
    assert food["weight_pct"] == "100.00%"
    # 基准含 5 只成分，食品饮料占 (5.32+1.98)/17.07 = 42.765085%
    assert food["benchmark_weight"] == "0.42765085"
    assert food["active_weight"] == "0.57234915"
    assert food["direction"] == "over"
    assert food["holding_count"] == 1
    assert food["market_value"] == portfolio["equity_market_value"]

    # 非持仓基准板块：市值 0、权重 null、低配
    for code in ("801050.SI", "801080.SI", "801180.SI"):
        row = by_code[code]
        assert row["market_value"] == "0.00"
        assert row["weight"] is None
        assert row["holding_count"] == 0
        assert row["direction"] == "under"
        assert row["active_weight"] == "-" + row["benchmark_weight"]

    concentration = body["concentration"]
    assert concentration["method"] == "industry_single_membership"
    assert concentration["status"] == "ok"
    assert concentration["hhi"] == "1.00000000"
    assert concentration["effective_n"] == "1.00000000"
    assert concentration["top1_weight"] == "1.00000000"
    assert concentration["top3_weight"] == "1.00000000"
    # 基准 HHI 按全部基准板块分布计算（4 个板块）
    assert concentration["benchmark_hhi"] == "0.29920185"
    # n_sectors 统计组合持仓涉及的板块数（与 HHI 计算口径一致，不含基准独占板块）
    assert concentration["n_sectors"] == 1

    preference = body["preference"]
    assert preference is not None
    assert preference["deviation"] == "0.57234915"
    assert preference["max_overweight"]["sector_code"] == "801120.SI"
    assert preference["max_underweight"]["sector_code"] == "801180.SI"
    assert [item["sector_code"] for item in preference["top_overweight"]] == ["801120.SI"]
    assert [item["sector_code"] for item in preference["top_underweight"]] == [
        "801180.SI",
        "801080.SI",
        "801050.SI",
    ]

    for key in (
        "total_assets",
        "holding_market_value",
        "equity_market_value",
        "mapped_market_value",
        "unclassified_market_value",
        "non_stock_market_value",
    ):
        assert _MONEY_RE.match(portfolio[key]), (key, portfolio[key])
    for key in ("coverage", "equity_position"):
        assert _RATIO_RE.match(portfolio[key]), (key, portfolio[key])
    for key in ("coverage_pct", "equity_position_pct"):
        assert _PCT_RE.match(portfolio[key]), (key, portfolio[key])


async def test_api_087_concept_taxonomy(
    sector_client: Any,
    account_factory: Any,
    owner_engine: AsyncEngine,
    settings: Any,
    frozen_clock: FrozenClock,
) -> None:
    acc = await account_factory()
    await _seed_stock_portfolio(
        sector_client, settings, acc.headers, frozen_clock, owner_engine, user_id=acc.user_id
    )

    resp = await sector_client.get(
        "/api/v1/sectors/overview", headers=acc.headers, params={"taxonomy": "concept"}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert body["taxonomy"] == "concept"
    assert body["level"] == 0
    assert body["weight_mode"] == "overlapping"
    assert body["benchmark_id"] is None
    assert body["preference"] is None
    assert body["concentration"]["method"] == "concept_overlap"
    assert body["concentration"]["status"] == "unavailable"
    assert body["concentration"]["hhi"] is None
    # 概念请求下行业映射专属字段为 null。
    for key in (
        "mapped_market_value",
        "unclassified_market_value",
        "coverage",
        "coverage_pct",
        "coverage_basis",
    ):
        assert body["portfolio"][key] is None

    block = body["concept"]
    assert block is not None
    assert block["coverage"] == "1.00000000"
    assert block["coverage_basis"] == "tagged_equity_market_value / equity_market_value"
    assert block["overlap"] == "2.00000000"
    assert block["caveat"]
    codes = {item["code"] for item in body["missing"]}
    assert "SECTOR_CONCEPT_MULTI_MEMBERSHIP" in codes
    assert "SECTOR_CONCEPT_HHI_UNAVAILABLE" in codes
    for row in body["sectors"]:
        assert row["level"] == 0
        assert row["parent_code"] is None
        assert row["exposure"] is not None
        assert row["weight"] is None
        assert row["benchmark_weight"] is None
        assert row["active_weight"] is None
        assert row["direction"] is None
    assert all(item["overlap"] is True for item in body["sectors"])


# ── 越权 / 身份 ─────────────────────────────────────────────


async def test_api_087_requires_token(sector_client: Any) -> None:
    resp = await sector_client.get("/api/v1/sectors/overview")
    assert resp.status_code == 401


async def test_api_087_admin_forbidden(sector_client: Any, account_factory: Any) -> None:
    admin = await account_factory(role="admin")
    resp = await sector_client.get("/api/v1/sectors/overview", headers=admin.headers)
    assert resp.status_code == 403
    assert resp.json()["code"] == "ADMIN_CANNOT_READ_TRADES"


async def test_api_087_cross_account_isolation(
    sector_client: Any,
    account_factory: Any,
    owner_engine: AsyncEngine,
    settings: Any,
    frozen_clock: FrozenClock,
) -> None:
    a = await account_factory()
    b = await account_factory()
    await _seed_stock_portfolio(
        sector_client,
        settings,
        a.headers,
        frozen_clock,
        owner_engine,
        user_id=a.user_id,
        codes=("600519",),
    )
    await _seed_stock_portfolio(
        sector_client,
        settings,
        b.headers,
        frozen_clock,
        owner_engine,
        user_id=b.user_id,
        codes=("000001",),
    )

    resp_a = await sector_client.get("/api/v1/sectors/overview", headers=a.headers)
    resp_b = await sector_client.get("/api/v1/sectors/overview", headers=b.headers)
    assert resp_a.status_code == 200, resp_a.text
    assert resp_b.status_code == 200, resp_b.text

    codes_a = {row["sector_code"] for row in resp_a.json()["sectors"]}
    codes_b = {row["sector_code"] for row in resp_b.json()["sectors"]}
    # 基准板块是公共缓存，两账号看到相同的板块集合；隔离体现在持仓市值上。
    assert codes_a == codes_b == {"801120.SI", "801050.SI", "801080.SI", "801180.SI"}
    rows_a = {row["sector_code"]: row for row in resp_a.json()["sectors"]}
    rows_b = {row["sector_code"]: row for row in resp_b.json()["sectors"]}
    # A 持仓食品饮料（600519），B 持仓银行（000001）；对方持仓板块市值为 0。
    assert rows_a["801120.SI"]["market_value"] != "0.00"
    assert rows_a["801050.SI"]["market_value"] == "0.00"
    assert rows_b["801050.SI"]["market_value"] != "0.00"
    assert rows_b["801120.SI"]["market_value"] == "0.00"
    # 响应里不出现对方持仓代码。
    assert "000001" not in resp_a.text
    assert "600519" not in resp_b.text


# ── 422 / 400 ───────────────────────────────────────────────


@pytest.mark.parametrize(
    ("params", "code"),
    [
        ({"as_of": "2026-07-02"}, "VALIDATION_ERROR"),
        ({"taxonomy": "nope"}, "VALIDATION_ERROR"),
        ({"level": 4}, "VALIDATION_ERROR"),
        ({"taxonomy": "concept", "level": 1}, "VALIDATION_ERROR"),
        ({"benchmark": "nope"}, "UNKNOWN_BENCHMARK"),
        ({"limit": 501}, "VALIDATION_ERROR"),
    ],
)
async def test_api_087_validation_and_unknown_benchmark(
    sector_client: Any, account_factory: Any, params: dict[str, Any], code: str
) -> None:
    acc = await account_factory()
    resp = await sector_client.get("/api/v1/sectors/overview", headers=acc.headers, params=params)
    assert resp.status_code == 422, resp.text
    assert resp.json()["code"] == code


async def test_api_087_unknown_query_param(sector_client: Any, account_factory: Any) -> None:
    acc = await account_factory()
    resp = await sector_client.get(
        "/api/v1/sectors/overview", headers=acc.headers, params={"foo": "bar"}
    )
    assert resp.status_code == 400, resp.text
    assert resp.json()["code"] == "UNKNOWN_QUERY_PARAM"


# ── 降级路径 ───────────────────────────────────────────────


async def test_api_087_assets_not_built_is_missing(
    sector_client: Any, account_factory: Any
) -> None:
    acc = await account_factory()
    resp = await sector_client.get("/api/v1/sectors/overview", headers=acc.headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "missing"
    assert body["asset_build_state"] == "missing"
    assert body["sectors"] == []
    assert body["portfolio"]["equity_market_value"] is None
    assert body["concentration"]["hhi"] is None
    assert any(item["code"] == "SECTOR_ASSET_BUILD_MISSING" for item in body["missing"])


async def test_api_087_no_stock_positions(
    sector_client: Any,
    account_factory: Any,
    owner_engine: AsyncEngine,
    settings: Any,
    frozen_clock: FrozenClock,
) -> None:
    acc = await account_factory()
    async with AsyncSession(owner_engine) as session:
        session.add(
            Trade(
                id=f"trd_{uuid.uuid4().hex[:12]}",
                user_id=acc.user_id,
                batch_id=None,
                trade_date=date(2026, 4, 1),
                security_code="",
                security_name="",
                side="other",
                quantity_units=0,
                price_units=0,
                amount_units=100_000_00,
                commission_units=0,
                stamp_tax_units=0,
                other_fee_units=0,
                cash_balance_units=100_000_00,
                cash_balance_available=True,
                category="transfer",
                occurrence_idx=0,
                source="import",
                version=1,
                summary="银行转证券",
                dedupe_key=f"transfer:{uuid.uuid4().hex[:10]}",
            )
        )
        await session.commit()
    await _build_assets(sector_client, settings, acc.headers, frozen_clock, owner_engine)

    resp = await sector_client.get("/api/v1/sectors/overview", headers=acc.headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "missing"
    assert body["portfolio"]["equity_market_value"] == "0.00"
    assert body["portfolio"]["coverage"] is None
    assert body["sectors"] == []
    assert any(item["code"] == "SECTOR_NO_STOCK_POSITIONS" for item in body["missing"])


async def test_api_087_history_before_first_sync_is_blocked(
    sector_client: Any,
    account_factory: Any,
    owner_engine: AsyncEngine,
    settings: Any,
    frozen_clock: FrozenClock,
) -> None:
    acc = await account_factory()
    await _seed_stock_portfolio(
        sector_client, settings, acc.headers, frozen_clock, owner_engine, user_id=acc.user_id
    )
    resp = await sector_client.get(
        "/api/v1/sectors/overview", headers=acc.headers, params={"as_of": "2026-06-26"}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "blocked"
    assert body["sectors"] == []
    assert body["portfolio"]["equity_market_value"] is None
    codes = {item["code"] for item in body["missing"]}
    assert "SECTOR_HISTORY_NOT_COVERED" in codes


# ── 只读 / ETag ────────────────────────────────────────────


async def test_api_087_read_only_and_etag_304(
    sector_client: Any,
    account_factory: Any,
    owner_engine: AsyncEngine,
    settings: Any,
    frozen_clock: FrozenClock,
) -> None:
    acc = await account_factory()
    await _seed_stock_portfolio(
        sector_client, settings, acc.headers, frozen_clock, owner_engine, user_id=acc.user_id
    )
    before = {
        sql: await _count(owner_engine, f"select count(*) from {sql}")
        for sql in ("platform.job", "review.asset_daily", "market.sector_member")
    }

    first = await sector_client.get("/api/v1/sectors/overview", headers=acc.headers)
    assert first.status_code == 200
    etag = first.headers["etag"]
    assert _HEX16_RE.match(etag.strip('"').split("-")[3])
    assert _HEX64_RE.match(first.json()["inputs_hash"])

    second = await sector_client.get(
        "/api/v1/sectors/overview",
        headers={**acc.headers, "If-None-Match": etag},
    )
    assert second.status_code == 304
    assert second.content == b""

    after = {
        sql: await _count(owner_engine, f"select count(*) from {sql}")
        for sql in ("platform.job", "review.asset_daily", "market.sector_member")
    }
    assert before == after
