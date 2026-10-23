"""`market` schema 缓存装饰器单测（真实 PostgreSQL）。

覆盖设计 `docs/architecture/2026-10-22-live-market-data-source.md` §4（D3=A+B）：

- 覆盖判定用区间并集：连续月份只拉缺口，已结束区间二次请求零网络；
- 尾部 `tail_refetch_days` 个自然日永远重拉（当月不漏新 bar）；
- 正缓存 TTL 30 天 / 负缓存短 TTL 1 天；
- `pull_range` 的 `range_end` 是覆盖上界（周末不重拉）、`data_end` 是上游最后交易日；
- `ON CONFLICT` 幂等，并发双击不炸；
- 用 **app 角色** 连接（验证迁移 0005 的 GRANT，owner 夹具会掩盖权限缺失）。
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Sequence
from datetime import date, timedelta
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import select
from stock_contracts.clock import FrozenClock
from stock_contracts.errors import ApiError, ErrorCode
from stock_platform.config import Settings
from stock_platform.db import Database
from stock_platform.market_live import CachedMarketDataSource, plan_fetch
from stock_platform.market_models import DailyBar, PullRange
from stock_platform.ports import Bar, MarketCapabilities, MarketDataSource, Quote

TODAY = date(2026, 10, 23)


class StubMarketDataSource:
    """可编程桩行情源：记录每次调用的 (symbols, start, end)。"""

    def __init__(self, bars: dict[str, list[Bar]] | None = None, *, delay: float = 0.0) -> None:
        self.calls: list[tuple[tuple[str, ...], date, date]] = []
        self._bars = bars or {}
        self._delay = delay

    @property
    def capabilities(self) -> MarketCapabilities:
        return MarketCapabilities(mode="live")

    @property
    def as_of(self) -> date:
        return TODAY

    @property
    def provenance(self) -> str:
        return "stub:market"

    async def get_daily_bars(self, symbols: Sequence[str], start: date, end: date) -> Sequence[Bar]:
        if self._delay:
            await asyncio.sleep(self._delay)
        self.calls.append((tuple(symbols), start, end))
        return tuple(
            bar
            for symbol in symbols
            for bar in self._bars.get(symbol, ())
            if start <= bar.trade_date <= end
        )

    async def get_latest_quotes(self, symbols: Sequence[str]) -> Sequence[Quote]:
        bars = await self.get_daily_bars(symbols, TODAY - timedelta(days=10), TODAY)
        last: dict[str, Bar] = {}
        for bar in bars:
            last[bar.symbol] = bar
        return tuple(
            Quote(symbol=symbol, as_of=bar.trade_date, close_units=bar.close_units)
            for symbol, bar in sorted(last.items())
        )


def _bars(symbol: str, *pairs: tuple[date, int]) -> list[Bar]:
    return [
        Bar(symbol=symbol, trade_date=trade_date, close_units=close_units, return_units=0)
        for trade_date, close_units in pairs
    ]


@pytest.fixture
def cache_settings(settings: Settings) -> Settings:
    return settings.model_copy(update={"market_mode": "live"})


@pytest.fixture
def clock() -> FrozenClock:
    return FrozenClock("2026-10-23T09:00:00+08:00")


@pytest_asyncio.fixture
async def app_db(settings: Settings) -> AsyncIterator[Database]:
    """app 角色连接：与生产一致（非 owner、受 GRANT 约束）。"""
    database = Database(settings, role="app")
    try:
        yield database
    finally:
        await database.dispose()


def _cache(
    db: Database, settings: Settings, clock: FrozenClock, inner: Any
) -> CachedMarketDataSource:
    return CachedMarketDataSource(inner, settings=settings, session_factory=db.session, clock=clock)


async def _daily_rows(db: Database) -> list[DailyBar]:
    async with db.session(begin=True) as session:
        return list((await session.execute(select(DailyBar))).scalars().all())


async def _pull_rows(db: Database) -> list[PullRange]:
    async with db.session(begin=True) as session:
        return list((await session.execute(select(PullRange))).scalars().all())


# ── 纯函数：覆盖判定 ────────────────────────────────────────
def test_plan_fetch_all_missing() -> None:
    assert plan_fetch(
        [], start=date(2021, 10, 1), end=date(2021, 10, 31), today=TODAY, tail_refetch_days=5
    ) == [(date(2021, 10, 1), date(2021, 10, 31))]


def test_plan_fetch_fully_covered_past_range_has_no_gap() -> None:
    # 覆盖上界是 requested_end（2021-10-31 是周日，也视为已覆盖，不再重拉）。
    gaps = plan_fetch(
        [(date(2021, 8, 17), date(2021, 10, 31))],
        start=date(2021, 9, 17),
        end=date(2021, 10, 31),
        today=TODAY,
        tail_refetch_days=5,
    )
    assert gaps == []


def test_plan_fetch_consecutive_months_only_gap() -> None:
    gaps = plan_fetch(
        [(date(2021, 9, 17), date(2021, 10, 31))],
        start=date(2021, 10, 17),
        end=date(2021, 11, 30),
        today=TODAY,
        tail_refetch_days=5,
    )
    assert gaps == [(date(2021, 11, 1), date(2021, 11, 30))]


def test_plan_fetch_tail_always_refetched() -> None:
    gaps = plan_fetch(
        [(date(2026, 9, 1), TODAY)],
        start=date(2026, 9, 1),
        end=TODAY,
        today=TODAY,
        tail_refetch_days=5,
    )
    assert gaps == [(TODAY - timedelta(days=5), TODAY)]


def test_plan_fetch_merges_multiple_gaps_into_one_fetch() -> None:
    gaps = plan_fetch(
        [(date(2021, 9, 1), date(2021, 9, 30))],
        start=date(2021, 8, 1),
        end=date(2021, 10, 31),
        today=TODAY,
        tail_refetch_days=5,
    )
    assert gaps[0][0] == date(2021, 8, 1)
    assert gaps[-1][1] == date(2021, 10, 31)


# ── 缓存行为（真实 DB）─────────────────────────────────────
async def test_miss_then_hit_persists_bars_and_range(
    app_db: Database, cache_settings: Settings, clock: FrozenClock
) -> None:
    stub = StubMarketDataSource(
        {
            "000651": _bars(
                "000651", (date(2021, 10, 28), 10_000_000), (date(2021, 10, 29), 10_100_000)
            )
        }
    )
    cache = _cache(app_db, cache_settings, clock, stub)

    bars = await cache.get_daily_bars(["000651"], date(2021, 10, 1), date(2021, 10, 31))
    assert len(bars) == 2
    assert len(stub.calls) == 1

    pull_rows = await _pull_rows(app_db)
    assert len(pull_rows) == 1
    assert pull_rows[0].bar_count == 2
    assert pull_rows[0].range_start == date(2021, 10, 1)
    assert pull_rows[0].range_end == date(2021, 10, 31)  # 覆盖上界 = 请求 end
    assert pull_rows[0].data_end == date(2021, 10, 29)  # 上游最后交易日
    assert pull_rows[0].complete is True
    assert pull_rows[0].checksum
    assert len(await _daily_rows(app_db)) == 2

    # 已结束区间二次请求：零网络
    again = await cache.get_daily_bars(["000651"], date(2021, 10, 1), date(2021, 10, 31))
    assert len(again) == 2
    assert len(stub.calls) == 1
    assert cache.as_of == date(2021, 10, 29)


async def test_consecutive_months_fetches_only_gap(
    app_db: Database, cache_settings: Settings, clock: FrozenClock
) -> None:
    stub = StubMarketDataSource(
        {
            "000651": _bars(
                "000651",
                (date(2021, 10, 29), 10_000_000),
                (date(2021, 11, 30), 10_200_000),
            )
        }
    )
    cache = _cache(app_db, cache_settings, clock, stub)

    await cache.get_daily_bars(["000651"], date(2021, 9, 17), date(2021, 10, 31))
    await cache.get_daily_bars(["000651"], date(2021, 10, 17), date(2021, 11, 30))

    assert len(stub.calls) == 2
    assert stub.calls[1][1] == date(2021, 11, 1)  # 只拉缺口
    assert stub.calls[1][2] == date(2021, 11, 30)


async def test_tail_is_refetched_every_request(
    app_db: Database, cache_settings: Settings, clock: FrozenClock
) -> None:
    stub = StubMarketDataSource(
        {"000651": _bars("000651", (TODAY - timedelta(days=1), 10_000_000))}
    )
    cache = _cache(app_db, cache_settings, clock, stub)

    await cache.get_daily_bars(["000651"], date(2026, 10, 1), TODAY)
    await cache.get_daily_bars(["000651"], date(2026, 10, 1), TODAY)

    assert len(stub.calls) == 2
    assert stub.calls[1][1] == TODAY - timedelta(days=5)  # 尾部窗口每次重拉


async def test_negative_cache_uses_short_ttl(
    app_db: Database, cache_settings: Settings, clock: FrozenClock
) -> None:
    stub = StubMarketDataSource({})
    cache = _cache(app_db, cache_settings, clock, stub)

    assert await cache.get_daily_bars(["000651"], date(2021, 10, 1), date(2021, 10, 31)) == ()
    assert len(stub.calls) == 1
    pull_rows = await _pull_rows(app_db)
    assert pull_rows[0].bar_count == 0
    assert pull_rows[0].data_end is None

    # 负缓存 TTL 内不重拉
    assert await cache.get_daily_bars(["000651"], date(2021, 10, 1), date(2021, 10, 31)) == ()
    assert len(stub.calls) == 1

    # 过期后重拉
    clock.advance(days=2)
    assert await cache.get_daily_bars(["000651"], date(2021, 10, 1), date(2021, 10, 31)) == ()
    assert len(stub.calls) == 2


async def test_store_is_idempotent_on_conflict(
    app_db: Database, cache_settings: Settings, clock: FrozenClock
) -> None:
    stub = StubMarketDataSource({})
    cache = _cache(app_db, cache_settings, clock, stub)
    rows = [(date(2021, 10, 28), 10_000_000, 0), (date(2021, 10, 29), 10_100_000, 1_000_000)]

    await cache._store("000651", date(2021, 10, 1), date(2021, 10, 31), rows, date(2021, 10, 29))
    await cache._store("000651", date(2021, 10, 1), date(2021, 10, 31), rows, date(2021, 10, 29))

    assert len(await _pull_rows(app_db)) == 1
    assert len(await _daily_rows(app_db)) == 2


async def test_concurrent_duplicate_requests_do_not_error(
    app_db: Database, cache_settings: Settings, clock: FrozenClock
) -> None:
    stub = StubMarketDataSource(
        {"000651": _bars("000651", (date(2021, 10, 29), 10_000_000))}, delay=0.05
    )
    cache = _cache(app_db, cache_settings, clock, stub)

    results = await asyncio.gather(
        cache.get_daily_bars(["000651"], date(2021, 10, 1), date(2021, 10, 31)),
        cache.get_daily_bars(["000651"], date(2021, 10, 1), date(2021, 10, 31)),
    )
    assert all(len(bars) == 1 for bars in results)
    assert len(await _pull_rows(app_db)) == 1


async def test_unsupported_symbol_fails_before_network(
    app_db: Database, cache_settings: Settings, clock: FrozenClock
) -> None:
    stub = StubMarketDataSource({})
    cache = _cache(app_db, cache_settings, clock, stub)
    with pytest.raises(ApiError) as caught:
        await cache.get_daily_bars(["510300"], date(2021, 10, 1), date(2021, 10, 31))
    assert caught.value.code is ErrorCode.NOT_FOUND
    assert stub.calls == []


async def test_latest_quotes_reads_through_cache(
    app_db: Database, cache_settings: Settings, clock: FrozenClock
) -> None:
    stub = StubMarketDataSource(
        {"000651": _bars("000651", (TODAY - timedelta(days=1), 10_000_000))}
    )
    cache = _cache(app_db, cache_settings, clock, stub)
    quotes = await cache.get_latest_quotes(["000651"])
    assert len(quotes) == 1
    assert quotes[0].as_of == TODAY - timedelta(days=1)

    assert isinstance(cache, MarketDataSource)
    assert cache.capabilities.mode == "live"
    assert cache.provenance == "stub:market#cache"
