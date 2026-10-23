"""API-027 验收：`GET /reconciliation/bridge`（桥接恒等式：期初+现金流+损益=期末）。

覆盖 INTERFACE §4.1：
* 现金链闭合 → balanced=true、|residual|≤0.01、status=ok，各项金额为 2 位定点字符串；
* 重复调用 inputs_hash / residual 完全相同（确定性）；
* 断链 fixture（期末余额不连续）→ balanced=false 且 residual≠0；
* 无任何 cash_balance → blocked、balanced=false、residual=0；
* INVALID_PERIOD（period 非法 / as_of 越界）→ 422；admin → 403；跨用户不串号。
"""

from __future__ import annotations

import re
import uuid
from datetime import date

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession
from stock_contracts.errors import ErrorCode
from stock_ledger.models import Trade

pytestmark = pytest.mark.api

_MONEY_RE = re.compile(r"^-?\d+\.\d{2}$")
_PERIOD = "2026-06"


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
) -> str:
    trade_id = f"trd_{uuid.uuid4().hex[:12]}"
    async with AsyncSession(engine) as session:
        session.add(
            Trade(
                id=trade_id,
                user_id=user_id,
                batch_id=None,
                trade_date=trade_date,
                security_code="600519",
                security_name="贵州茅台",
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
                summary="证券买入" if side == "buy" else "证券卖出",
                dedupe_key=f"600519:{uuid.uuid4().hex[:10]}",
            )
        )
        await session.commit()
    return trade_id


async def _seed_closed_chain(engine: AsyncEngine, *, user_id: uuid.UUID) -> None:
    """手算闭合链：
    05-31 买 100@100（-10000，余额 100000）→ 期初现金 100000 / 期初持仓成本 10000
    06-10 卖 100@120（+12000，余额 112000）→ 平仓损益 2000
    06-20 买 50@100（-5000，余额 107000）→ 期末持仓成本 5000
    → 100000 + 10000 + 0 + 2000 + 0 + 0 - 0 + 0 == 107000 + 5000
    """
    await _seed_trade(
        engine,
        user_id=user_id,
        trade_date=date(2026, 5, 31),
        side="buy",
        quantity_units=1_000_000,
        price_units=100_000_000,
        amount_units=-1_000_000,
        cash_balance_units=10_000_000,
        category="buy",
    )
    await _seed_trade(
        engine,
        user_id=user_id,
        trade_date=date(2026, 6, 10),
        side="sell",
        quantity_units=1_000_000,
        price_units=120_000_000,
        amount_units=1_200_000,
        cash_balance_units=11_200_000,
        category="sell",
    )
    await _seed_trade(
        engine,
        user_id=user_id,
        trade_date=date(2026, 6, 20),
        side="buy",
        quantity_units=500_000,
        price_units=100_000_000,
        amount_units=-500_000,
        cash_balance_units=10_700_000,
        category="buy",
    )


async def _bridge(client, api_prefix, headers, period: str = _PERIOD) -> dict:
    resp = await client.get(f"{api_prefix}/reconciliation/bridge?period={period}", headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


async def test_api_027_balanced_identity(
    client, api_prefix, user, owner_engine: AsyncEngine
) -> None:
    await _seed_closed_chain(owner_engine, user_id=user.user_id)
    body = await _bridge(client, api_prefix, user.headers)

    assert body["period"] == _PERIOD
    assert body["balanced"] is True, body
    assert body["status"] == "ok", body
    assert body["tolerance"] == "0.01"
    identity = body["identity"]
    expected = {
        "initial_cash": "100000.00",
        "initial_open_cost": "10000.00",
        "cashflows": "0.00",
        "realized_pnl": "2000.00",
        "dividends": "0.00",
        "interest": "0.00",
        "fees": "0.00",
        "other": "0.00",
        "open_cost": "5000.00",
        "ending_value": "112000.00",
        "residual": "0.00",
    }
    for field, value in expected.items():
        assert identity[field] == value, f"{field}: {identity[field]} != {value}"
        assert _MONEY_RE.match(identity[field]), f"{field} 不是定点字符串"
    assert abs(float(identity["residual"])) <= 0.01
    # core 的 inputs_hash 是规范化 JSON 的 sha256（64 位 hex；文档示例前缀 sha256: 仅为展示）。
    assert re.fullmatch(r"(?:sha256:)?[0-9a-f]{64}", body["inputs_hash"]), body["inputs_hash"]


async def test_api_027_repeated_calls_are_deterministic(
    client, api_prefix, user, owner_engine: AsyncEngine
) -> None:
    await _seed_closed_chain(owner_engine, user_id=user.user_id)
    first = await _bridge(client, api_prefix, user.headers)
    second = await _bridge(client, api_prefix, user.headers)
    assert first["inputs_hash"] == second["inputs_hash"]
    assert first["identity"] == second["identity"]
    assert first["balanced"] == second["balanced"] is True


async def test_api_027_broken_chain_residual_nonzero(
    client, api_prefix, user, owner_engine: AsyncEngine
) -> None:
    await _seed_trade(
        owner_engine,
        user_id=user.user_id,
        trade_date=date(2026, 5, 31),
        side="buy",
        quantity_units=1_000_000,
        price_units=100_000_000,
        amount_units=-1_000_000,
        cash_balance_units=10_000_000,
        category="buy",
    )
    await _seed_trade(
        owner_engine,
        user_id=user.user_id,
        trade_date=date(2026, 6, 10),
        side="sell",
        quantity_units=1_000_000,
        price_units=120_000_000,
        amount_units=1_200_000,
        cash_balance_units=11_200_000,
        category="sell",
    )
    # 期末余额被写错 2000.00：链不闭合 → 桥接残差 = 2000.00
    await _seed_trade(
        owner_engine,
        user_id=user.user_id,
        trade_date=date(2026, 6, 20),
        side="buy",
        quantity_units=500_000,
        price_units=100_000_000,
        amount_units=-500_000,
        cash_balance_units=10_500_000,
        category="buy",
    )
    body = await _bridge(client, api_prefix, user.headers)
    assert body["balanced"] is False
    assert body["identity"]["residual"] != "0.00"
    assert abs(float(body["identity"]["residual"])) > 0.01


async def test_api_027_blocked_without_cash_balance(
    client, api_prefix, user, owner_engine: AsyncEngine
) -> None:
    await _seed_trade(
        owner_engine,
        user_id=user.user_id,
        trade_date=date(2026, 6, 10),
        side="buy",
        quantity_units=1_000_000,
        price_units=100_000_000,
        amount_units=-1_000_000,
        cash_balance_units=None,
        category="buy",
    )
    body = await _bridge(client, api_prefix, user.headers)
    assert body["status"] == "blocked"
    assert body["balanced"] is False
    assert body["identity"]["residual"] == "0.00"


async def test_api_027_invalid_period_422(client, api_prefix, user) -> None:
    for period in ("2026-13", "2026-1", "not-a-period"):
        resp = await client.get(
            f"{api_prefix}/reconciliation/bridge?period={period}", headers=user.headers
        )
        assert resp.status_code == 422, period
        assert resp.json()["code"] == ErrorCode.INVALID_PERIOD.value

    outside = await client.get(
        f"{api_prefix}/reconciliation/bridge?period=2026-06&as_of=2026-07-01",
        headers=user.headers,
    )
    assert outside.status_code == 422
    assert outside.json()["code"] == ErrorCode.INVALID_PERIOD.value


async def test_api_027_as_of_narrows_period(
    client, api_prefix, user, owner_engine: AsyncEngine
) -> None:
    await _seed_closed_chain(owner_engine, user_id=user.user_id)
    resp = await client.get(
        f"{api_prefix}/reconciliation/bridge?period=2026-06&as_of=2026-06-15",
        headers=user.headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # 截至 06-15：卖 100 已清仓，未含 06-20 的买入 → 期末现金 112000、持仓成本 0。
    assert body["identity"]["ending_value"] == "112000.00"
    assert body["identity"]["open_cost"] == "0.00"
    assert body["balanced"] is True


async def test_api_027_cross_user_and_admin(
    client, api_prefix, user, account_factory, admin, owner_engine: AsyncEngine
) -> None:
    await _seed_closed_chain(owner_engine, user_id=user.user_id)
    owner_body = await _bridge(client, api_prefix, user.headers)

    intruder = await account_factory()
    intruder_body = await _bridge(client, api_prefix, intruder.headers)
    assert intruder_body["status"] == "blocked"
    assert intruder_body["identity"]["initial_cash"] == "0.00"
    assert intruder_body["identity"] != owner_body["identity"], "B 的桥接看到了 A 的数据"

    forbidden = await client.get(
        f"{api_prefix}/reconciliation/bridge?period={_PERIOD}", headers=admin.headers
    )
    assert forbidden.status_code == 403
    assert forbidden.json()["code"] == ErrorCode.ADMIN_CANNOT_READ_TRADES.value
