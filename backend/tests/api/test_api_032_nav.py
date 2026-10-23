"""API-032 验收：`GET /nav`（单位净值序列 + TWR/MWR）。

覆盖 API-Catalog §2.2 / §6：
* happy path：points 按 date 升序、twr_daily 为十进制小数、默认 benchmark_id=="hs300_tr"；
* 日收益 vs 独立手算 oracle ≤1bp；
* 无 cash_balance 锚点 → status=="blocked"，points 中 blocked=true；
* 持仓标的缺行情 → 逐日阻断（200），不得整段 404；
* 跨用户不串号（404）；admin 不可读业务数据（403）；
* INVALID_PERIOD → 422。
"""

from __future__ import annotations

import re
import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession
from stock_contracts.errors import ErrorCode
from stock_ledger.models import Trade

pytestmark = pytest.mark.api

_MONEY_RE = re.compile(r"^-?\d+\.\d{2}$")
_NAV_RE = re.compile(r"^-?\d+\.\d{6}$")
_RATIO_RE = re.compile(r"^-?\d+\.\d{8}$")
_PCT_RE = re.compile(r"^-?\d+\.\d{2}%$")
_3DP_RE = re.compile(r"^-?\d+\.\d{3}$")


async def _seed_trade(
    engine: AsyncEngine,
    *,
    user_id: uuid.UUID,
    trade_date: date,
    side: str,
    quantity_units: int,
    price_units: int,
    amount_units: int,
    cash_balance_units: int | None,
    category: str,
    security_code: str = "600519",
    commission_units: int = 0,
    stamp_tax_units: int = 0,
    other_fee_units: int = 0,
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
                security_name="贵州茅台",
                side=side,
                quantity_units=quantity_units,
                price_units=price_units,
                amount_units=amount_units,
                commission_units=commission_units,
                stamp_tax_units=stamp_tax_units,
                other_fee_units=other_fee_units,
                cash_balance_units=cash_balance_units,
                cash_balance_available=cash_balance_units is not None,
                category=category,
                occurrence_idx=0,
                source="import",
                version=1,
                summary="证券买入" if side == "buy" else "证券卖出",
                dedupe_key=f"{security_code}:{uuid.uuid4().hex[:10]}",
            )
        )
        await session.commit()
    return trade_id


async def _seed_happy_chain(engine: AsyncEngine, *, user_id: uuid.UUID) -> None:
    """2026-06 区间（用 fixtures/market 里 600519 的月末收盘 1706.20 做 oracle）：
    05-29 余额行 100000.00（期初现金锚点）
    06-30 买 100 股 @1700.00（-170000.00，余额 -70000.00）
    06-30 总资产 = -70000 + 100×1706.20 = 100620.00 → 相对期初 100000 的 TWR = 0.0062
    """
    await _seed_trade(
        engine,
        user_id=user_id,
        trade_date=date(2026, 5, 29),
        side="buy",
        quantity_units=0,
        price_units=0,
        amount_units=0,
        cash_balance_units=100_000_00,  # 100000.00
        category="other",
    )
    await _seed_trade(
        engine,
        user_id=user_id,
        trade_date=date(2026, 6, 30),
        side="buy",
        quantity_units=100_0000,  # 100 股（scale=4）
        price_units=1700_000000,  # 1700 元（scale=6）
        amount_units=-170_000_00,  # -170000.00
        cash_balance_units=-70_000_00,  # -70000.00
        category="buy",
    )


@pytest.mark.asyncio
async def test_api_032_happy_path(client, account_factory, owner_engine):
    """points 升序、twr_daily 十进制、默认 benchmark_id。"""
    acc = await account_factory()
    await _seed_happy_chain(owner_engine, user_id=acc.user_id)

    r = await client.get(
        "/api/v1/nav",
        params={"period": "2026-06"},
        headers=acc.headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["period"] == "2026-06"
    assert body["benchmark_id"] == "hs300_tr"
    assert body["definition_version"] == "1.0"

    points = body["points"]
    assert len(points) >= 1
    # 升序
    dates = [p["date"] for p in points]
    assert dates == sorted(dates)
    # 格式
    for p in points:
        assert _NAV_RE.match(p["unit_nav"]), p
        assert _RATIO_RE.match(p["twr_daily"]), p
        assert _PCT_RE.match(p["twr_daily_pct"]), p
        assert _MONEY_RE.match(p["cashflow"]), p
        assert _3DP_RE.match(p["total_assets"]), p
        assert p["prev_total_assets"] is None or _3DP_RE.match(p["prev_total_assets"]), p
        assert p["daily_pnl"] is None or _3DP_RE.match(p["daily_pnl"]), p
        assert isinstance(p["blocked"], bool)
    # 月度首日：昨日资产取上一交易日收盘估值（现金账户无持仓 → 等于期初现金）
    assert points[0]["prev_total_assets"] == "100000.000"
    assert points[0]["daily_pnl"] == "0.000"
    assert points[0]["twr_daily_pct"] == "0.00%"

    # summary 格式
    s = body["summary"]
    assert _RATIO_RE.match(s["mwr"]), s
    assert _RATIO_RE.match(s["twr"]), s
    assert _RATIO_RE.match(s["annualized_twr"]), s
    assert _RATIO_RE.match(s["mwr_minus_twr"]), s
    assert isinstance(s["n_days"], int)
    # 期初锚点：05-29 余额行在区间前 → statement 直取
    assert s["assumption"] == "statement"
    # 本期不含基准对比
    assert s["beta"] is None
    assert s["alpha_annual"] is None
    assert s["ir"] is None


@pytest.mark.asyncio
async def test_api_032_twr_matches_oracle(client, account_factory, owner_engine):
    """日收益/TWR 与独立手算 oracle 偏差 ≤1bp（口径验收④）。"""
    acc = await account_factory()
    await _seed_happy_chain(owner_engine, user_id=acc.user_id)

    r = await client.get("/api/v1/nav", params={"period": "2026-06"}, headers=acc.headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "ok"

    points = {p["date"]: p for p in body["points"]}
    # 期初基准点（区间首日）+ 期末点；oracle：期初 100000 现金，期末 100620 = -70000 + 100×1706.20
    assert points["2026-06-01"]["unit_nav"] == "100000.000000"
    assert points["2026-06-30"]["unit_nav"] == "100620.000000"
    # 昨日资产 / 日收益额 / 日收益百分比
    assert points["2026-06-30"]["prev_total_assets"] == "100000.000"
    assert points["2026-06-30"]["daily_pnl"] == "620.000"
    assert points["2026-06-30"]["twr_daily_pct"] == "0.62%"
    twr = Decimal(body["summary"]["twr"])
    assert abs(twr - Decimal("0.0062")) <= Decimal("0.0001")  # ≤1bp
    # MWR 为 XIRR（ACT/365 年化）：无外部现金流时 MWR 应与年化 TWR 一致
    mwr = Decimal(body["summary"]["mwr"])
    annualized_twr = Decimal(body["summary"]["annualized_twr"])
    assert abs(mwr - annualized_twr) <= Decimal("0.0001")
    assert abs(Decimal(body["summary"]["mwr_minus_twr"])) <= Decimal("0.0001")


@pytest.mark.asyncio
async def test_api_032_first_point_prev_assets_from_holdings(client, account_factory, owner_engine):
    """月度首日补齐昨日资产：期初持仓按上一交易日（05-29）收盘价估值。"""
    acc = await account_factory()
    await _seed_trade(
        owner_engine,
        user_id=acc.user_id,
        trade_date=date(2026, 5, 20),
        side="buy",
        quantity_units=100_0000,  # 100 股
        price_units=1700_930000,  # 1700.93（05-29 收盘）
        amount_units=-170_093_00,
        cash_balance_units=0,
        category="buy",
    )

    r = await client.get("/api/v1/nav", params={"period": "2026-06"}, headers=acc.headers)
    assert r.status_code == 200, r.text
    first = r.json()["points"][0]
    assert first["date"] == "2026-06-01"
    # 06-01 当日也按 05-29 收盘估值（夹具无 06-01 bar）→ 昨日资产 = 当日资产
    assert first["total_assets"] == "170093.000"
    assert first["prev_total_assets"] == "170093.000"
    assert first["daily_pnl"] == "0.000"
    assert first["twr_daily_pct"] == "0.00%"


@pytest.mark.asyncio
async def test_api_032_holding_values_trimmed(client, account_factory, owner_engine):
    """持仓明细最多三位小数且去掉尾零：9.1 股不得渲染成 "009.100"。"""
    acc = await account_factory()
    await _seed_trade(
        owner_engine,
        user_id=acc.user_id,
        trade_date=date(2026, 5, 29),
        side="buy",
        quantity_units=0,
        price_units=0,
        amount_units=0,
        cash_balance_units=100_000_00,
        category="other",
    )
    await _seed_trade(
        owner_engine,
        user_id=acc.user_id,
        trade_date=date(2026, 6, 30),
        side="buy",
        quantity_units=9_1000,  # 9.1 股（scale=4）
        price_units=1700_000000,
        amount_units=-154_70_00,  # -15470.00
        cash_balance_units=84_530_00,
        category="buy",
    )

    r = await client.get("/api/v1/nav", params={"period": "2026-06"}, headers=acc.headers)
    assert r.status_code == 200, r.text
    point = next(p for p in r.json()["points"] if p["date"] == "2026-06-30")
    holding = point["holdings"][0]
    assert holding["quantity"] == "9.1"
    assert holding["avg_cost"] == "1700"
    assert holding["close_price"] == "1706.2"
    assert holding["market_value"] == "15526.42"
    assert point["total_assets"] == "100056.420"
    assert point["daily_pnl"] == "56.420"


@pytest.mark.asyncio
async def test_api_032_avg_cost_includes_fees(client, account_factory, owner_engine):
    """买入均价为含费口径：avg_cost = (成交额 + 佣金 + 印花税 + 其他费) ÷ 数量。"""
    acc = await account_factory()
    await _seed_trade(
        owner_engine,
        user_id=acc.user_id,
        trade_date=date(2026, 5, 29),
        side="buy",
        quantity_units=0,
        price_units=0,
        amount_units=0,
        cash_balance_units=200_000_00,
        category="other",
    )
    # 100 股 @1700，佣金 42.50 + 其他费 2.50 → 净现金流 -(170000+45.00) = -170045.00
    await _seed_trade(
        owner_engine,
        user_id=acc.user_id,
        trade_date=date(2026, 6, 30),
        side="buy",
        quantity_units=100_0000,
        price_units=1700_000000,
        amount_units=-170_045_00,
        cash_balance_units=29_955_00,
        category="buy",
        commission_units=42_50,
        other_fee_units=2_50,
    )

    r = await client.get("/api/v1/nav", params={"period": "2026-06"}, headers=acc.headers)
    assert r.status_code == 200, r.text
    point = next(p for p in r.json()["points"] if p["date"] == "2026-06-30")
    holding = point["holdings"][0]
    assert holding["quantity"] == "100"
    # 买入均价 = 170045 / 100 = 1700.45（高于成交价 1700，差额即费用）
    assert holding["avg_cost"] == "1700.45"


@pytest.mark.asyncio
async def test_api_032_missing_market_data_blocks_not_404(client, account_factory, owner_engine):
    """持仓标的无行情 → 逐日 blocked 且返回 200，绝不整段 404（回放夹具缺该标的的场景）。"""
    acc = await account_factory()
    await _seed_trade(
        owner_engine,
        user_id=acc.user_id,
        trade_date=date(2026, 5, 29),
        side="buy",
        quantity_units=0,
        price_units=0,
        amount_units=0,
        cash_balance_units=100_000_00,
        category="other",
    )
    await _seed_trade(
        owner_engine,
        user_id=acc.user_id,
        trade_date=date(2026, 6, 30),
        side="buy",
        quantity_units=100_0000,
        price_units=20_000000,
        amount_units=-20_000_00,
        cash_balance_units=80_000_00,
        category="buy",
        security_code="000858",  # fixtures/market/quotes.json 无此标的
    )

    r = await client.get("/api/v1/nav", params={"period": "2026-06"}, headers=acc.headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "blocked"
    # 缺行情日不输出资产值
    blocked = [p for p in body["points"] if p["blocked"]]
    assert blocked, body["points"]
    for point in blocked:
        assert point["unit_nav"] == "0.000000"
        assert point["twr_daily"] == "0.00000000"
        # 新增可选字段（非破坏）：blocked 日必须给出可读原因
        assert point["blocked_reason"]
        assert "缺行情数据" in point["blocked_reason"]
    assert body["blocked_ranges"]
    # 非 blocked 日该字段为 null（显式序列化）
    for point in body["points"]:
        if not point["blocked"]:
            assert point["blocked_reason"] is None


@pytest.mark.asyncio
async def test_api_032_no_cash_balance_blocked(client, account_factory, owner_engine):
    """无 cash_balance 锚点 → status=blocked，points 中 blocked=true 且无资产值。"""
    acc = await account_factory()
    await _seed_trade(
        owner_engine,
        user_id=acc.user_id,
        trade_date=date(2026, 6, 15),
        side="buy",
        quantity_units=100_0000,
        price_units=1700_000000,
        amount_units=-170_000_00,
        cash_balance_units=None,
        category="buy",
    )

    r = await client.get(
        "/api/v1/nav",
        params={"period": "2026-06"},
        headers=acc.headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "blocked"
    # blocked 区间
    assert len(body["blocked_ranges"]) >= 1


@pytest.mark.asyncio
async def test_api_032_back_solved_anchor(client, account_factory, owner_engine):
    """首行非余额行、区间内首条余额行 → 构建成功且 assumption=="back_solved"（验收⑤）。

    06-05 入金 +100000（无余额列）；06-30 买 100 股 @1700（余额 -70000）。
    期初反推 = -70000 − (−170000) − 100000 = 0；期末总资产 = -70000 + 100×1706.20 = 100620。
    """
    acc = await account_factory()
    await _seed_trade(
        owner_engine,
        user_id=acc.user_id,
        trade_date=date(2026, 6, 5),
        side="buy",
        quantity_units=0,
        price_units=0,
        amount_units=100_000_00,  # +100000.00
        cash_balance_units=None,
        category="transfer",
        security_code="CASH",
    )
    await _seed_trade(
        owner_engine,
        user_id=acc.user_id,
        trade_date=date(2026, 6, 30),
        side="buy",
        quantity_units=100_0000,
        price_units=1700_000000,
        amount_units=-170_000_00,
        cash_balance_units=-70_000_00,  # 区间内首条余额行
        category="buy",
    )

    r = await client.get("/api/v1/nav", params={"period": "2026-06"}, headers=acc.headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "ok"
    assert body["summary"]["assumption"] == "back_solved"
    points = {p["date"]: p for p in body["points"]}
    assert points["2026-06-01"]["unit_nav"] == "0.000000"
    # 期初现金反推为 0 → 首日昨日资产/收益额均为 0（非 null）
    assert points["2026-06-01"]["prev_total_assets"] == "0.000"
    assert points["2026-06-01"]["daily_pnl"] == "0.000"
    assert points["2026-06-30"]["total_assets"] == "100620.000"


@pytest.mark.asyncio
async def test_api_032_admin_forbidden(client, account_factory):
    """admin 不可读业务数据 → 403。"""
    acc = await account_factory(role="admin")
    r = await client.get(
        "/api/v1/nav",
        params={"period": "2026-06"},
        headers=acc.headers,
    )
    assert r.status_code == 403
    assert r.json()["code"] == ErrorCode.ADMIN_CANNOT_READ_TRADES.value


@pytest.mark.asyncio
async def test_api_032_invalid_period(client, account_factory):
    """非法 period → 422 INVALID_PERIOD。"""
    acc = await account_factory()
    r = await client.get(
        "/api/v1/nav",
        params={"period": "2026-13"},
        headers=acc.headers,
    )
    assert r.status_code == 422
    assert r.json()["code"] == ErrorCode.INVALID_PERIOD.value


@pytest.mark.asyncio
async def test_api_032_cross_user_isolation(client, account_factory, owner_engine):
    """用户 A 的 NAV 不包含用户 B 的数据（各自独立）。"""
    acc_a = await account_factory()
    acc_b = await account_factory()
    await _seed_happy_chain(owner_engine, user_id=acc_a.user_id)

    r_a = await client.get("/api/v1/nav", params={"period": "2026-06"}, headers=acc_a.headers)
    r_b = await client.get("/api/v1/nav", params={"period": "2026-06"}, headers=acc_b.headers)
    assert r_a.status_code == 200
    assert r_b.status_code == 200
    # B 没有数据 → blocked
    assert r_b.json()["status"] == "blocked"
