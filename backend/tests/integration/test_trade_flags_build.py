"""账单级旗标按月构建集成测试：持仓推演 + 幂等 + 命中/边界状态。"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import date, timedelta

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession
from stock_contracts.clock import FrozenClock
from stock_platform.ports import Bar, MarketCapabilities, Quote
from stock_review.metrics.context import parse_period_label
from stock_review.models import TradeFlag
from stock_review.trade_flags import build_trade_flags_for_user, load_trade_flags

pytestmark = [pytest.mark.integration]

CLOCK = FrozenClock("2026-10-06T12:00:00+08:00")
SYMBOL = "600000"
BENCH = "hs300_tr"
CALENDAR = tuple(date(2026, 8, 20) + timedelta(days=i) for i in range(48))

INSERT_TRADE_SQL = """
insert into ledger.trade (
    id, user_id, trade_date, security_code, security_name, summary, side,
    quantity_units, price_units, amount_units, commission_units, stamp_tax_units,
    other_fee_units, cash_balance_units, cash_balance_available, category,
    dedupe_key, occurrence_idx, source, version, line_no
) values (
    :id, :user_id, :trade_date, :security_code, '测试股', '', :side,
    :quantity_units, :price_units, :amount_units, 0, 0,
    0, null, false, 'trade',
    :dedupe_key, 0, 'import', 1, :line_no
)
"""


class FakeMarket:
    """回放行情桩：固定收盘价，覆盖全窗口。"""

    capabilities = MarketCapabilities(mode="replay", symbols=(SYMBOL, BENCH))

    def __init__(self, close_units: int) -> None:
        self._close = close_units

    @property
    def as_of(self) -> date:
        return CALENDAR[-1]

    @property
    def provenance(self) -> str:
        return "test"

    async def get_daily_bars(self, symbols: Sequence[str], start: date, end: date) -> Sequence[Bar]:
        return [
            Bar(symbol=symbol, trade_date=day, close_units=self._close, return_units=0)
            for symbol in symbols
            for day in CALENDAR
            if start <= day <= end
        ]

    async def get_latest_quotes(self, symbols: Sequence[str]) -> Sequence[Quote]:
        return ()


async def _insert_user(engine: AsyncEngine) -> uuid.UUID:
    user_id = uuid.uuid4()
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "insert into platform.user (id, email, password_hash, role, status) "
                "values (:id, :email, 'x', 'user', 'active')"
            ),
            {"id": user_id, "email": f"{user_id.hex[:12]}@example.com"},
        )
    return user_id


async def _seed_trade(
    engine: AsyncEngine,
    user_id: uuid.UUID,
    *,
    trade_date: date,
    side: str,
    quantity_units: int,
    amount_units: int,
    line_no: int,
) -> str:
    trade_id = f"trd_{uuid.uuid4().hex[:12]}"
    async with engine.begin() as conn:
        await conn.execute(
            text(INSERT_TRADE_SQL),
            {
                "id": trade_id,
                "user_id": user_id,
                "trade_date": trade_date,
                "security_code": SYMBOL,
                "side": side,
                "quantity_units": quantity_units,
                "price_units": 10_000_000,
                "amount_units": amount_units,
                "dedupe_key": f"{trade_id}:1",
                "line_no": line_no,
            },
        )
    return trade_id


async def _build(session: AsyncSession, user_id: uuid.UUID, dry_run: bool = False):
    period = parse_period_label("2026-09", today=CLOCK.today())
    return await build_trade_flags_for_user(
        session,
        user_id,
        period=period,
        market_data=FakeMarket(close_units=10_000_000),
        clock=CLOCK,
        dry_run=dry_run,
    )


@pytest.mark.asyncio
async def test_build_is_idempotent_and_hits_expected_flags(owner_engine: AsyncEngine) -> None:
    user_id = await _insert_user(owner_engine)
    await _seed_trade(
        owner_engine,
        user_id,
        trade_date=date(2026, 9, 10),
        side="buy",
        quantity_units=1000,
        amount_units=100,
        line_no=1,
    )
    await _seed_trade(
        owner_engine,
        user_id,
        trade_date=date(2026, 9, 11),
        side="buy",
        quantity_units=1000,
        amount_units=100,
        line_no=2,
    )
    await _seed_trade(
        owner_engine,
        user_id,
        trade_date=date(2026, 9, 15),
        side="sell",
        quantity_units=2000,
        amount_units=220,
        line_no=3,
    )

    async with AsyncSession(owner_engine) as session:
        first = await _build(session, user_id)
        await session.commit()
    assert first.trades == 3
    assert first.rows == 6 + 6 + 7  # 买入 6 项、卖出 7 项

    async with AsyncSession(owner_engine) as session:
        rows = list(await session.scalars(select(TradeFlag).where(TradeFlag.user_id == user_id)))
    assert len(rows) == first.rows
    hits = {(row.trade_date, row.flag_code) for row in rows if row.status == "hit"}
    assert (date(2026, 9, 11), "addon_average") in hits
    assert (date(2026, 9, 15), "full_exit") in hits
    assert all(
        row.status == "not_applicable"
        for row in rows
        if row.trade_date == date(2026, 9, 10) and row.flag_code.startswith("addon_")
    )

    async with AsyncSession(owner_engine) as session:
        second = await _build(session, user_id)
        await session.commit()
    assert second.rows == first.rows
    async with AsyncSession(owner_engine) as session:
        again = list(await session.scalars(select(TradeFlag).where(TradeFlag.user_id == user_id)))
    assert len(again) == first.rows

    async with AsyncSession(owner_engine) as session:
        flagged = await load_trade_flags(session, user_id, [rows[0].trade_dedupe_key])
    assert flagged


@pytest.mark.asyncio
async def test_dry_run_writes_nothing(owner_engine: AsyncEngine) -> None:
    user_id = await _insert_user(owner_engine)
    await _seed_trade(
        owner_engine,
        user_id,
        trade_date=date(2026, 9, 10),
        side="buy",
        quantity_units=1000,
        amount_units=100,
        line_no=1,
    )
    async with AsyncSession(owner_engine) as session:
        result = await _build(session, user_id, dry_run=True)
        await session.commit()
    assert result.dry_run is True
    assert result.rows > 0
    async with AsyncSession(owner_engine) as session:
        rows = list(await session.scalars(select(TradeFlag).where(TradeFlag.user_id == user_id)))
    assert rows == []
