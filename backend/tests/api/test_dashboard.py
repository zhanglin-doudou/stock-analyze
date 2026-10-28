"""API-084 验收（Period2-12）：`GET /dashboard` 两板块聚合。

覆盖设计 §6.2 / §8.1：
* happy path：资产概览 headline=最新资产日、历史概览=全历史（计数/最赚最亏/回撤）；
  15 字段（§1.3 A1~A7、H1~H8）存在且格式为定点字符串；
* 未构建 → 200 + `build_state=missing` + 金额全 null（不用 0 冒充）+ missing 码；
* 全局 blocked → 200 + `build_state=blocked` + 金额全 null + `DASHBOARD_ASSET_BLOCKED`；
* 有效观测 < 60 → `sample_status=insufficient` 且 `mdd_pct=null` + missing 码；
* 跨用户：B 200 且不含 A 的代码/金额；admin 403；无 token 401；
* 只读：调用前后 `platform.job` / `review.asset_daily` 行数不变。

时钟口径：`GET /dashboard` 的 `build_state` 依赖 `clock.today()`，因此本文件用
`create_app(settings, clock=FrozenClock(...))` 装配确定性 app，并用同一 FrozenClock
驱动 `WorkerRunner` 构建资产表。
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
from stock_review.models import Round
from stock_worker.runner import WorkerRunner

pytestmark = pytest.mark.api

_MONEY_RE = re.compile(r"^-?\d+\.\d{2}$")
_RATIO_RE = re.compile(r"^-?\d+\.\d{8}$")
_QTY_RE = re.compile(r"^-?\d+\.\d{4}$")
_PCT_RE = re.compile(r"^-?\d+\.\d{2}%$")

_CLOCK = "2026-07-01T09:00:00+00:00"


@pytest.fixture
def frozen_clock() -> FrozenClock:
    return FrozenClock(_CLOCK)


@pytest_asyncio.fixture
async def dashboard_client(settings: Any, frozen_clock: FrozenClock) -> Any:
    """与 worker 同钟的确定性 ASGI 客户端。"""
    app = create_app(settings, clock=frozen_clock)
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client


async def _drain_worker(settings: Any, *, clock: FrozenClock, owner_engine: AsyncEngine) -> None:
    """把队列里的任务全部执行完（available_at 拨到过去，模拟执行时刻已到）。"""
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
        "/api/v1/assets/build",
        headers={**headers, "Idempotency-Key": f"dash-{uuid.uuid4().hex}"},
    )
    assert resp.status_code == 202, resp.text
    await _drain_worker(settings, clock=clock, owner_engine=owner_engine)


async def _seed_trade(
    engine: AsyncEngine,
    *,
    user_id: uuid.UUID,
    trade_date: date,
    side: str = "buy",
    quantity_units: int = 0,
    price_units: int = 0,
    amount_units: int = 0,
    cash_balance_units: int | None = None,
    category: str = "other",
    security_code: str = "600519",
    summary: str = "证券买入",
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


async def _seed_happy_chain(engine: AsyncEngine, *, user_id: uuid.UUID) -> None:
    """04-01 现金锚点 100000.00（0 金额利息行）+ 06-30 买入 100@1700（余额 -70000）。

    锚点用 0 金额利息行而非 `other`：桥接把区间内 `other` 现金流视为"未分类"并整体
    degraded（D-02），会让 `total_profit` 置 null；利息行 0 金额不触发降级。
    """
    await _seed_trade(
        engine,
        user_id=user_id,
        trade_date=date(2026, 4, 1),
        amount_units=0,
        cash_balance_units=100_000_00,
        category="interest",
        security_code="",
        summary="利息归本",
    )
    await _seed_trade(
        engine,
        user_id=user_id,
        trade_date=date(2026, 6, 30),
        quantity_units=100_0000,
        price_units=1700_000000,
        amount_units=-170_000_00,
        cash_balance_units=-70_000_00,
        category="buy",
    )


async def _seed_short_chain(engine: AsyncEngine, *, user_id: uuid.UUID) -> None:
    """有效观测约 34 天（< 60）→ 回撤 insufficient。"""
    await _seed_trade(
        engine,
        user_id=user_id,
        trade_date=date(2026, 5, 29),
        amount_units=0,
        cash_balance_units=100_000_00,
        category="interest",
        security_code="",
        summary="利息归本",
    )
    await _seed_trade(
        engine,
        user_id=user_id,
        trade_date=date(2026, 6, 30),
        quantity_units=100_0000,
        price_units=1700_000000,
        amount_units=-170_000_00,
        cash_balance_units=-70_000_00,
        category="buy",
    )


async def _seed_anchor_null_return_chain(engine: AsyncEngine, *, user_id: uuid.UUID) -> None:
    """首日转入（balance == amount）→ 期初现金 0，首日 prev_total=0，日收益必然为 NULL。

    这正是真实数据（1234@dev.com）的形态：首日锚点行无法计算日收益，但它不是缺口，
    不应触发 ``DASHBOARD_MDD_GAPS``。
    """
    await _seed_trade(
        engine,
        user_id=user_id,
        trade_date=date(2026, 4, 1),
        amount_units=100_000_00,
        cash_balance_units=100_000_00,
        category="transfer",
        security_code="",
        summary="银行转证券",
    )
    await _seed_trade(
        engine,
        user_id=user_id,
        trade_date=date(2026, 6, 30),
        quantity_units=100_0000,
        price_units=1700_000000,
        amount_units=-170_000_00,
        cash_balance_units=-70_000_00,
        category="buy",
    )


async def _seed_interior_gap_chain(engine: AsyncEngine, *, user_id: uuid.UUID) -> None:
    """04-01 转入 → 05-15 全额转出（余额 0）→ 06-01 重新转入 → 06-30 买入。

    05-16 ~ 06-01 的 prev_total=0，日收益 NULL，构成*中途*断链（应报
    ``DASHBOARD_MDD_GAPS``），与首日锚点行区分。
    """
    await _seed_trade(
        engine,
        user_id=user_id,
        trade_date=date(2026, 4, 1),
        amount_units=100_000_00,
        cash_balance_units=100_000_00,
        category="transfer",
        security_code="",
        summary="银行转证券",
    )
    await _seed_trade(
        engine,
        user_id=user_id,
        trade_date=date(2026, 5, 15),
        amount_units=-100_000_00,
        cash_balance_units=0,
        category="transfer",
        security_code="",
        summary="证券转银行",
    )
    await _seed_trade(
        engine,
        user_id=user_id,
        trade_date=date(2026, 6, 1),
        amount_units=100_000_00,
        cash_balance_units=100_000_00,
        category="transfer",
        security_code="",
        summary="银行转证券",
    )
    await _seed_trade(
        engine,
        user_id=user_id,
        trade_date=date(2026, 6, 30),
        quantity_units=100_0000,
        price_units=1700_000000,
        amount_units=-170_000_00,
        cash_balance_units=-70_000_00,
        category="buy",
    )


async def _seed_no_anchor(engine: AsyncEngine, *, user_id: uuid.UUID) -> None:
    """全部 cash_balance 为 NULL → 全局 blocked。"""
    await _seed_trade(
        engine,
        user_id=user_id,
        trade_date=date(2026, 6, 1),
        quantity_units=100_0000,
        price_units=1700_000000,
        amount_units=-170_000_00,
        category="buy",
    )


async def _seed_rounds(engine: AsyncEngine, *, user_id: uuid.UUID) -> None:
    """3 笔已平仓（2 盈 1 亏，3 只股票）：600519 +2000、000001 +1000、000858 -500。"""
    now = datetime(2026, 6, 30, 15, 0, tzinfo=UTC)
    specs = [
        ("600519", "贵州茅台", 200_000),
        ("000001", "平安银行", 100_000),
        ("000858", "五粮液", -50_000),
    ]
    async with AsyncSession(engine) as session:
        for code, name, pnl in specs:
            session.add(
                Round(
                    id=f"rnd_{uuid.uuid4().hex[:12]}",
                    user_id=user_id,
                    security_code=code,
                    security_name=name,
                    status="closed",
                    open_trade_id=f"trd_open_{code}",
                    close_trade_id=f"trd_close_{code}",
                    open_date=date(2026, 6, 1),
                    close_date=date(2026, 6, 10),
                    quantity_units=100_0000,
                    cost_units=1_000_000_000,
                    proceeds_units=1_100_000_000,
                    dividend_units=0,
                    pnl_units=pnl,
                    return_rate_units=10_000_000 if pnl > 0 else -5_000_000,
                    holding_days=5,
                    data_error=False,
                    oversell_units=0,
                    fills_count=1,
                    rebuilt_at=now,
                )
            )
        await session.commit()


async def _count(engine: AsyncEngine, sql: str) -> int:
    async with engine.begin() as conn:
        return int(await conn.scalar(text(sql)) or 0)


def _find(missing: list[dict[str, str]], code: str) -> dict[str, str] | None:
    return next((item for item in missing if item["code"] == code), None)


# ── happy path ───────────────────────────────────────────────


async def test_api_084_happy_path_two_sections(
    dashboard_client: Any,
    account_factory: Any,
    owner_engine: AsyncEngine,
    settings: Any,
    frozen_clock: FrozenClock,
) -> None:
    acc = await account_factory()
    await _seed_happy_chain(owner_engine, user_id=acc.user_id)
    await _seed_rounds(owner_engine, user_id=acc.user_id)
    await _build_assets(dashboard_client, settings, acc.headers, frozen_clock, owner_engine)

    resp = await dashboard_client.get("/api/v1/dashboard", headers=acc.headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert body["status"] == "ok"
    assert body["build_state"] == "current"
    assert body["as_of"] == "2026-07-01"
    assert body["data_as_of"] == "2026-09-30"
    assert body["legacy_period"] == "2026-06"
    assert body["missing"] == []

    asset = body["asset_overview"]
    assert asset["status"] == "ok"
    assert asset["market_value"] == "170620.00"
    assert asset["cash"] == "-70000.00"
    assert asset["total_assets"] == "100620.00"
    assert asset["position_pct"] == "169.57%"
    assert asset["total_profit"] == "620.00"
    assert asset["max_invested_cost"] == "170000.00"
    assert asset["max_cost_return_pct"] == "0.36%"
    assert asset["total_fees"] == "0.00"
    for field in ("market_value", "cash", "total_assets", "total_profit", "max_invested_cost"):
        assert _MONEY_RE.match(asset[field]), (field, asset[field])
    assert _PCT_RE.match(asset["position_pct"])

    holdings = asset["holdings"]
    assert len(holdings) == 1
    holding = holdings[0]
    assert holding["security_code"] == "600519"
    assert holding["quantity"] == "100.0000"
    assert holding["cost"] == "170000.00"
    assert holding["market_value"] == "170620.00"
    assert holding["weight_pct"] == "1.00000000"
    assert _QTY_RE.match(holding["quantity"])
    assert _RATIO_RE.match(holding["weight_pct"])
    # DASH-02：持仓市值合计 == headline 市值
    assert sum(int(item["market_value"].replace(".", "")) for item in holdings) == int(
        asset["market_value"].replace(".", "")
    )

    history = body["history_overview"]
    assert history["status"] == "ok"
    assert history["closed_security_count"] == 3
    assert history["closed_round_count"] == 3
    assert (history["win_count"], history["loss_count"], history["flat_count"]) == (2, 1, 0)
    assert history["win_pct"] == "66.67%"
    assert history["loss_pct"] == "33.33%"
    assert history["max_profit"] == "2000.00"
    assert history["max_profit_round"]["security_code"] == "600519"
    assert history["max_profit_cost_pct"] == "1.18%"
    assert [item["security_code"] for item in history["top_rounds"]] == [
        "600519",
        "000001",
        "000858",
    ]
    assert history["bottom_rounds"][0]["security_code"] == "000858"
    assert _PCT_RE.match(history["win_pct"])
    assert _PCT_RE.match(history["max_profit_cost_pct"])

    drawdown = history["max_drawdown"]
    assert drawdown["sample_status"] == "ok"
    assert drawdown["mdd_pct"] == "0.00%"
    assert drawdown["n_obs"] >= 60
    assert drawdown["from_date"] is None and drawdown["to_date"] is None
    assert drawdown["reason"] is None

    assert body["links"] == {
        "assets": "/assets",
        "rounds": "/rounds",
        "metrics": "/metrics?period=2026-06",
        "reports": "/reports",
        "exports": "/exports",
        "attribution": "/attribution?period=2026-06",
    }
    assert body["assumptions"]["total_profit_basis"] == "full_history_bridge"
    assert re.match(r"^[0-9a-f]{64}$", body["inputs_hash"])


# ── missing / blocked / insufficient ─────────────────────────


async def test_api_084_not_built_is_missing(dashboard_client: Any, account_factory: Any) -> None:
    acc = await account_factory()
    resp = await dashboard_client.get("/api/v1/dashboard", headers=acc.headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert body["status"] == "missing"
    assert body["build_state"] == "missing"
    assert body["data_as_of"] is None
    assert body["legacy_period"] == "2026-07"

    asset = body["asset_overview"]
    assert asset["status"] == "missing"
    assert asset["holdings"] == []
    for field in (
        "market_value",
        "cash",
        "total_assets",
        "position_pct",
        "total_profit",
        "max_invested_cost",
        "max_cost_return_pct",
        "total_fees",
    ):
        assert asset[field] is None, (field, asset[field])

    history = body["history_overview"]
    assert history["closed_security_count"] == 0
    assert history["closed_round_count"] == 0
    assert history["win_pct"] is None
    assert history["max_profit"] is None
    assert history["max_drawdown"]["sample_status"] == "blocked"
    assert history["max_drawdown"]["mdd_pct"] is None

    codes = {item["code"] for item in body["missing"]}
    assert "DASHBOARD_ASSET_MISSING" in codes
    assert "DASHBOARD_NO_CLOSED_ROUNDS" in codes
    assert "DASHBOARD_TOTAL_PROFIT_UNAVAILABLE" in codes


async def test_api_084_blocked_has_no_numbers(
    dashboard_client: Any,
    account_factory: Any,
    owner_engine: AsyncEngine,
    settings: Any,
    frozen_clock: FrozenClock,
) -> None:
    acc = await account_factory()
    await _seed_no_anchor(owner_engine, user_id=acc.user_id)
    await _build_assets(dashboard_client, settings, acc.headers, frozen_clock, owner_engine)

    resp = await dashboard_client.get("/api/v1/dashboard", headers=acc.headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert body["status"] == "blocked"
    assert body["build_state"] == "blocked"
    asset = body["asset_overview"]
    assert asset["status"] == "blocked"
    assert asset["holdings"] == []
    for field in (
        "market_value",
        "cash",
        "total_assets",
        "position_pct",
        "total_profit",
        "max_invested_cost",
        "max_cost_return_pct",
        "total_fees",
    ):
        assert asset[field] is None, (field, asset[field])

    blocked = _find(body["missing"], "DASHBOARD_ASSET_BLOCKED")
    assert blocked is not None
    assert blocked["reason"]
    assert blocked["what_to_do"]


async def test_api_084_insufficient_mdd(
    dashboard_client: Any,
    account_factory: Any,
    owner_engine: AsyncEngine,
    settings: Any,
    frozen_clock: FrozenClock,
) -> None:
    acc = await account_factory()
    await _seed_short_chain(owner_engine, user_id=acc.user_id)
    await _seed_rounds(owner_engine, user_id=acc.user_id)
    await _build_assets(dashboard_client, settings, acc.headers, frozen_clock, owner_engine)

    resp = await dashboard_client.get("/api/v1/dashboard", headers=acc.headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert body["status"] == "insufficient"
    drawdown = body["history_overview"]["max_drawdown"]
    assert drawdown["sample_status"] == "insufficient"
    assert drawdown["mdd_pct"] is None
    assert drawdown["from_date"] is None
    assert drawdown["n_obs"] < 60
    assert _find(body["missing"], "DASHBOARD_MDD_INSUFFICIENT") is not None


async def test_api_084_leading_anchor_null_return_is_not_a_gap(
    dashboard_client: Any,
    account_factory: Any,
    owner_engine: AsyncEngine,
    settings: Any,
    frozen_clock: FrozenClock,
) -> None:
    """首日锚点行 daily_return=NULL 不是缺口：不得报 DASHBOARD_MDD_GAPS（回归）。"""
    acc = await account_factory()
    await _seed_anchor_null_return_chain(owner_engine, user_id=acc.user_id)
    await _seed_rounds(owner_engine, user_id=acc.user_id)
    await _build_assets(dashboard_client, settings, acc.headers, frozen_clock, owner_engine)

    resp = await dashboard_client.get("/api/v1/dashboard", headers=acc.headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert _find(body["missing"], "DASHBOARD_MDD_GAPS") is None
    assert body["status"] == "ok"
    assert body["history_overview"]["status"] == "ok"
    assert body["missing"] == []

    drawdown = body["history_overview"]["max_drawdown"]
    assert drawdown["sample_status"] == "ok"
    assert drawdown["mdd_pct"] == "0.00%"
    # 92 个自然日 − 首日锚点 1 天 = 91 个有效观测
    assert drawdown["n_obs"] == 91


async def test_api_084_interior_null_return_still_reports_gaps(
    dashboard_client: Any,
    account_factory: Any,
    owner_engine: AsyncEngine,
    settings: Any,
    frozen_clock: FrozenClock,
) -> None:
    """中途 prev_total=0（清仓后空档）仍必须断链并报 DASHBOARD_MDD_GAPS。"""
    acc = await account_factory()
    await _seed_interior_gap_chain(owner_engine, user_id=acc.user_id)
    await _seed_rounds(owner_engine, user_id=acc.user_id)
    await _build_assets(dashboard_client, settings, acc.headers, frozen_clock, owner_engine)

    resp = await dashboard_client.get("/api/v1/dashboard", headers=acc.headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert _find(body["missing"], "DASHBOARD_MDD_GAPS") is not None
    assert body["asset_overview"]["status"] == "ok"
    assert body["history_overview"]["status"] == "degraded"
    assert body["status"] == "degraded"

    drawdown = body["history_overview"]["max_drawdown"]
    assert drawdown["sample_status"] == "ok"
    assert drawdown["mdd_pct"] == "0.00%"
    # 92 天 − 首日锚点 1 天 − 中途空档 17 天 = 74 个有效观测
    assert drawdown["n_obs"] == 74


# ── 隔离 / admin / 未认证 ───────────────────────────────────


async def test_api_084_cross_user_isolation(
    dashboard_client: Any,
    account_factory: Any,
    owner_engine: AsyncEngine,
    settings: Any,
    frozen_clock: FrozenClock,
) -> None:
    owner = await account_factory()
    intruder = await account_factory()
    await _seed_happy_chain(owner_engine, user_id=owner.user_id)
    await _seed_rounds(owner_engine, user_id=owner.user_id)
    await _build_assets(dashboard_client, settings, owner.headers, frozen_clock, owner_engine)

    resp = await dashboard_client.get("/api/v1/dashboard", headers=intruder.headers)
    assert resp.status_code == 200, resp.text
    text_body = resp.text
    assert "600519" not in text_body
    assert str(owner.user_id) not in text_body
    body = resp.json()
    assert body["build_state"] == "missing"
    assert body["asset_overview"]["holdings"] == []
    assert body["asset_overview"]["market_value"] is None
    assert body["history_overview"]["closed_round_count"] == 0

    owner_resp = await dashboard_client.get("/api/v1/dashboard", headers=owner.headers)
    assert owner_resp.status_code == 200
    assert owner_resp.json()["asset_overview"]["holdings"][0]["security_code"] == "600519"


async def test_api_084_admin_forbidden(dashboard_client: Any, account_factory: Any) -> None:
    admin = await account_factory(role="admin")
    resp = await dashboard_client.get("/api/v1/dashboard", headers=admin.headers)
    assert resp.status_code == 403, resp.text
    assert resp.json()["code"] == ErrorCode.ADMIN_CANNOT_READ_TRADES.value


async def test_api_084_unauthenticated_401(dashboard_client: Any) -> None:
    resp = await dashboard_client.get("/api/v1/dashboard")
    assert resp.status_code == 401, resp.text
    assert resp.json()["code"] == ErrorCode.TOKEN_EXPIRED.value


# ── 只读：无写副作用 ────────────────────────────────────────


async def test_api_084_is_read_only(
    dashboard_client: Any,
    account_factory: Any,
    owner_engine: AsyncEngine,
    settings: Any,
    frozen_clock: FrozenClock,
) -> None:
    acc = await account_factory()
    await _seed_happy_chain(owner_engine, user_id=acc.user_id)
    await _seed_rounds(owner_engine, user_id=acc.user_id)
    await _build_assets(dashboard_client, settings, acc.headers, frozen_clock, owner_engine)

    jobs_before = await _count(owner_engine, "select count(*) from platform.job")
    rows_before = await _count(owner_engine, "select count(*) from review.asset_daily")

    for _ in range(2):
        resp = await dashboard_client.get("/api/v1/dashboard", headers=acc.headers)
        assert resp.status_code == 200, resp.text

    assert await _count(owner_engine, "select count(*) from platform.job") == jobs_before
    assert await _count(owner_engine, "select count(*) from review.asset_daily") == rows_before
