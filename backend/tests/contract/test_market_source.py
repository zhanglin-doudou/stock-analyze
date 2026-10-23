"""`MarketDataSource` 端口契约参数化测试（Extensibility X-3 / EP-2）。

同一套断言跑多个实现（replay / akshare mock），保证「换实现不改调用方」。缓存装饰器
需要真实 DB，其端口行为由 `backend/tests/unit/platform/test_market_cache.py` 覆盖。
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from typing import Any

import pytest
from stock_contracts.clock import FrozenClock
from stock_contracts.errors import ApiError, ErrorCode
from stock_ledger.market import ReplayMarketDataSource
from stock_platform import market_live
from stock_platform.config import Settings
from stock_platform.ports import Bar, MarketDataSource, Quote

REPLAY_SYMBOL = "000001"
REPLAY_START = date(2026, 1, 1)
REPLAY_END = date(2026, 9, 30)
LIVE_SYMBOL = "600519"
LIVE_START = date(2021, 10, 1)
LIVE_END = date(2021, 10, 31)


class _Frame:
    def __init__(self, records: list[dict[str, Any]]) -> None:
        self._records = records

    def to_dict(self, orient: str) -> list[dict[str, Any]]:
        assert orient == "records"
        return self._records


class _FakeAkshare:
    __version__ = "9.9.9"

    @staticmethod
    def stock_zh_a_hist(**_kwargs: Any) -> _Frame:
        return _Frame(
            [
                {"日期": date(2021, 10, 28), "收盘": 10.0},
                {"日期": date(2021, 10, 29), "收盘": 10.5},
            ]
        )


@pytest.fixture(params=["replay", "akshare"])
def source_case(
    request: pytest.FixtureRequest, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> tuple[MarketDataSource, str, date, date]:
    if request.param == "replay":
        source = ReplayMarketDataSource(settings.fixtures_dir / "market" / "quotes.json")
        return source, REPLAY_SYMBOL, REPLAY_START, REPLAY_END
    monkeypatch.setattr(market_live, "_import_akshare", lambda: _FakeAkshare)
    live_settings = Settings(market_mode="live", market_live_cache_enabled=False)
    clock = FrozenClock("2026-10-23T09:00:00+08:00")
    return (
        market_live.AkshareMarketDataSource(live_settings, clock),
        LIVE_SYMBOL,
        LIVE_START,
        LIVE_END,
    )


async def test_capabilities_are_declared_truthfully(
    source_case: tuple[MarketDataSource, str, date, date],
) -> None:
    source, _symbol, _start, _end = source_case
    assert isinstance(source, MarketDataSource)
    capabilities = source.capabilities
    assert capabilities.mode in {"replay", "live"}
    assert capabilities.supports_daily_bars is True
    assert capabilities.supports_latest_quote is True
    assert capabilities.max_lookback_days is None or capabilities.max_lookback_days > 0


async def test_as_of_and_provenance_are_declared(
    source_case: tuple[MarketDataSource, str, date, date],
) -> None:
    source, _symbol, _start, _end = source_case
    assert isinstance(source.as_of, date)
    assert isinstance(source.provenance, str) and source.provenance


async def test_argument_validation(
    source_case: tuple[MarketDataSource, str, date, date],
) -> None:
    source, symbol, start, end = source_case
    with pytest.raises(ApiError) as empty:
        await source.get_daily_bars([], start, end)
    assert empty.value.code is ErrorCode.VALIDATION_ERROR
    with pytest.raises(ApiError) as reversed_range:
        await source.get_daily_bars([symbol], end, start)
    assert reversed_range.value.code is ErrorCode.VALIDATION_ERROR


async def test_daily_bars_are_sorted_and_positive(
    source_case: tuple[MarketDataSource, str, date, date],
) -> None:
    source, symbol, start, end = source_case
    bars = await source.get_daily_bars([symbol], start, end)
    assert bars, "契约测试要求至少返回一条 bar"
    assert all(isinstance(bar, Bar) for bar in bars)
    assert all(bar.symbol == symbol for bar in bars)
    assert all(bar.close_units > 0 for bar in bars)
    assert list(bars) == sorted(bars, key=lambda bar: (bar.trade_date, bar.symbol))
    assert all(start <= bar.trade_date <= end for bar in bars)


async def test_latest_quotes_returns_quotes(
    source_case: tuple[MarketDataSource, str, date, date],
) -> None:
    source, symbol, _start, _end = source_case
    quotes = await source.get_latest_quotes([symbol])
    assert quotes
    assert all(isinstance(quote, Quote) for quote in quotes)
    assert all(quote.close_units > 0 for quote in quotes)
    assert all(isinstance(quote.as_of, date) for quote in quotes)


async def test_sequence_types_are_immutable_results(
    source_case: tuple[MarketDataSource, str, date, date],
) -> None:
    """端口返回 Sequence；调用方不得依赖 list 特有行为（统一转 tuple）。"""
    source, symbol, start, end = source_case
    bars: Sequence[Bar] = await source.get_daily_bars([symbol], start, end)
    assert len({(bar.trade_date, bar.symbol) for bar in bars}) == len(bars)
