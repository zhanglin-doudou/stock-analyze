"""akshare 行情适配器单测（不联网、不依赖 DB）。

覆盖设计 `docs/architecture/2026-10-22-live-market-data-source.md` §5：

- 列名/类型边界：中文列、`date`/字符串日期、float64 收盘 → 标度整数（禁 float 进内部）；
- NaN/None/负价/重复日期/坏日期 → fail-closed（`INTERNAL_ERROR` + `invalid_data`）；
- 标的校验：A 股主板/科创/创业 + 场内基金（沪 5、深 15/16/18）放行；其余 `NOT_FOUND` + `not_supported`；
- 股票多源：东财 → 新浪 → 腾讯（退市股兜底）；基金多源：东财基金 → 新浪基金（全量过滤）；
- 真超时：`timeout=` 必须传给 akshare；瞬时故障重试耗尽 → `INTERNAL_ERROR` + `unavailable`；
- 空 DataFrame（合法标的）→ 空序列（不抛）；
- `as_of` / `capabilities` / `provenance` / `get_latest_quotes` 语义。
"""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest
from stock_contracts.clock import FrozenClock
from stock_contracts.errors import ApiError, ErrorCode
from stock_platform import market_live
from stock_platform.config import Settings
from stock_platform.ports import MarketDataSource

TODAY = date(2026, 10, 23)


class Timeout(Exception):  # noqa: N818 - 与 requests.Timeout 同名，验证按类名识别
    """瞬时故障替身（类名命中 `_TRANSIENT_EXC_NAMES`）。"""


class _Frame:
    """最小 DataFrame 替身：只实现 `to_dict("records")`。"""

    def __init__(self, records: list[dict[str, Any]]) -> None:
        self._records = records

    def to_dict(self, orient: str) -> list[dict[str, Any]]:
        assert orient == "records"
        return self._records


class _FakeAkshare:
    __version__ = "9.9.9"

    def __init__(
        self,
        handler: Any,
        sina_handler: Any | None = None,
        tx_handler: Any | None = None,
        fund_em_handler: Any | None = None,
        fund_sina_handler: Any | None = None,
    ) -> None:
        # stock_zh_a_hist（东财，中文列名）/ stock_zh_a_daily（新浪，英文列名）/
        # stock_zh_a_hist_tx（腾讯，英文列名）/ fund_etf_hist_em / fund_etf_hist_sina
        # 是真实 akshare 里的独立接口，这里允许测试分别注入。
        # tx 默认跟随 sina（保持既有「东财挂→兜底也挂」测试的调用计数语义）。
        self.stock_zh_a_hist = handler
        self.stock_zh_a_daily = sina_handler if sina_handler is not None else handler
        self.stock_zh_a_hist_tx = tx_handler if tx_handler is not None else self.stock_zh_a_daily
        self.fund_etf_hist_em = fund_em_handler if fund_em_handler is not None else handler
        self.fund_etf_hist_sina = fund_sina_handler if fund_sina_handler is not None else handler


def _sina_fallback_transient(**_kwargs: Any) -> _Frame:
    """新浪在东财 TransientError 场景下也抛 TransientError，让外层 retry 接管。"""
    raise Timeout("sina down")


def _sina_success(*pairs: tuple[date, float]) -> Any:
    """新浪正常返回时用英文列名（date/close），与 _records 的中文列名对齐。"""

    def _handler(**_kwargs: Any) -> _Frame:
        return _Frame([{"date": d, "close": c} for d, c in pairs])

    return _handler


def _settings(**overrides: Any) -> Settings:
    defaults: dict[str, Any] = {
        "market_mode": "live",
        "market_live_retry_backoff_seconds": 0.0,
        "market_live_timeout_seconds": 10.0,
    }
    defaults.update(overrides)
    return Settings(**defaults)


def _clock() -> FrozenClock:
    return FrozenClock("2026-10-23T09:00:00+08:00")


def _source(
    handler: Any,
    monkeypatch: pytest.MonkeyPatch,
    *,
    sina_handler: Any | None = None,
    tx_handler: Any | None = None,
    fund_em_handler: Any | None = None,
    fund_sina_handler: Any | None = None,
    **overrides: Any,
) -> Any:
    monkeypatch.setattr(
        market_live,
        "_import_akshare",
        lambda: _FakeAkshare(
            handler,
            sina_handler=sina_handler,
            tx_handler=tx_handler,
            fund_em_handler=fund_em_handler,
            fund_sina_handler=fund_sina_handler,
        ),
    )
    return market_live.AkshareMarketDataSource(_settings(**overrides), _clock())


def _records(*pairs: tuple[date, float]) -> list[dict[str, Any]]:
    return [{"日期": trade_date, "收盘": close} for trade_date, close in pairs]


def _noop_handler(**_kwargs: Any) -> _Frame:
    raise AssertionError("不应调用 akshare")


# ── 行解析 / 定点转换 ────────────────────────────────────────
def test_parse_rows_converts_close_to_units_and_derives_returns() -> None:
    bars, data_end = market_live._parse_rows(
        _records((date(2021, 10, 28), 10.0), (date(2021, 10, 29), 10.5))
    )
    assert data_end == date(2021, 10, 29)
    assert bars == [
        (date(2021, 10, 28), 10_000_000, 0),
        (date(2021, 10, 29), 10_500_000, 5_000_000),  # +5% → scale=8 → 5e6
    ]


def test_parse_rows_accepts_string_and_compact_dates() -> None:
    bars, data_end = market_live._parse_rows(
        [
            {"日期": "2021-10-28", "收盘": "10.00"},
            {"日期": "20211029", "收盘": "10.50"},
        ]
    )
    assert [bar[0] for bar in bars] == [date(2021, 10, 28), date(2021, 10, 29)]
    assert data_end == date(2021, 10, 29)


@pytest.mark.parametrize(
    "row",
    [
        {"日期": date(2021, 10, 28), "收盘": None},
        {"日期": date(2021, 10, 28), "收盘": float("nan")},
        {"日期": date(2021, 10, 28), "收盘": float("inf")},
        {"日期": date(2021, 10, 28), "收盘": "abc"},
        {"日期": "not-a-date", "收盘": 10.0},
        {"日期": date(2021, 10, 28), "收盘": 0.0},
        {"日期": date(2021, 10, 28), "收盘": -1.0},
    ],
)
def test_parse_rows_fails_closed_on_bad_values(row: dict[str, Any]) -> None:
    with pytest.raises(market_live._InvalidMarketDataError):
        market_live._parse_rows([row])


def test_parse_rows_rejects_duplicate_dates() -> None:
    with pytest.raises(market_live._InvalidMarketDataError):
        market_live._parse_rows(_records((date(2021, 10, 28), 10.0), (date(2021, 10, 28), 10.5)))


# ── 标的校验 ────────────────────────────────────────────────
@pytest.mark.parametrize(
    "symbol", ["600519", "601688", "603000", "688001", "000651", "002594", "300750"]
)
def test_validate_symbols_accepts_a_share_prefixes(symbol: str) -> None:
    assert market_live.validate_symbols([symbol]) == (symbol,)


@pytest.mark.parametrize(
    "symbol",
    [
        "510300",  # 沪 ETF
        "159915",  # 深 ETF
        "501018",  # 沪 LOF
        "161039",  # 深 LOF
        "508099",  # 沪 REITs
        "184801",  # 深 REITs
    ],
)
def test_validate_symbols_accepts_fund_prefixes(symbol: str) -> None:
    assert market_live.validate_symbols([symbol]) == (symbol,)


@pytest.mark.parametrize("symbol", ["113001", "900901", "200002", "830799"])
def test_validate_symbols_rejects_unsupported_products(symbol: str) -> None:
    with pytest.raises(ApiError) as caught:
        market_live.validate_symbols([symbol])
    assert caught.value.code is ErrorCode.NOT_FOUND
    assert caught.value.metadata[market_live.FAILURE_KEY] == market_live.FAILURE_NOT_SUPPORTED


def test_validate_symbols_rejects_empty_and_invalid() -> None:
    with pytest.raises(ApiError) as caught:
        market_live.validate_symbols([])
    assert caught.value.code is ErrorCode.VALIDATION_ERROR
    with pytest.raises(ApiError) as invalid:
        market_live.validate_symbols(["abc"])
    # 非 6 位数字同样按「不在支持范围」处理（fail-closed，NAV 侧降级为 blocked）
    assert invalid.value.code is ErrorCode.NOT_FOUND
    assert invalid.value.metadata[market_live.FAILURE_KEY] == market_live.FAILURE_NOT_SUPPORTED


# ── 端口契约 / 调用约定 ─────────────────────────────────────
async def test_daily_bars_passes_timeout_and_no_adjust(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, Any]] = []

    def handler(**kwargs: Any) -> _Frame:
        calls.append(kwargs)
        return _Frame(_records((date(2021, 10, 28), 10.0), (date(2021, 10, 29), 10.5)))

    source = _source(handler, monkeypatch)
    assert isinstance(source, MarketDataSource)
    bars = await source.get_daily_bars(["600519"], date(2021, 10, 1), date(2021, 10, 31))

    assert [bar.trade_date for bar in bars] == [date(2021, 10, 28), date(2021, 10, 29)]
    assert bars[0].close_units == 10_000_000
    assert bars[1].return_units == 5_000_000
    assert calls[0]["adjust"] == ""
    assert calls[0]["timeout"] == 10.0
    assert calls[0]["start_date"] == "20211001"
    assert calls[0]["end_date"] == "20211031"
    assert source.as_of == date(2021, 10, 29)
    assert source.capabilities.mode == "live"
    assert source.capabilities.symbols == ()
    assert source.capabilities.supports_adjusted is False
    assert source.provenance == "live:akshare@9.9.9"


async def test_transient_error_is_retried_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = 0

    def handler(**_kwargs: Any) -> _Frame:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise Timeout("connect timeout")
        return _Frame(_records((date(2021, 10, 28), 10.0)))

    source = _source(
        handler, monkeypatch, market_live_retry_attempts=2, sina_handler=_sina_fallback_transient
    )
    bars = await source.get_daily_bars(["600519"], date(2021, 10, 1), date(2021, 10, 31))
    assert len(bars) == 1
    assert calls == 2


async def test_transient_error_exhausted_maps_to_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    def handler(**_kwargs: Any) -> _Frame:
        nonlocal calls
        calls += 1
        raise Timeout("down")

    source = _source(
        handler, monkeypatch, market_live_retry_attempts=2, sina_handler=_sina_fallback_transient
    )
    with pytest.raises(ApiError) as caught:
        await source.get_daily_bars(["600519"], date(2021, 10, 1), date(2021, 10, 31))
    assert caught.value.code is ErrorCode.INTERNAL_ERROR
    assert caught.value.metadata[market_live.FAILURE_KEY] == market_live.FAILURE_UNAVAILABLE
    assert caught.value.problem.retryable is True
    assert calls == 2


async def test_invalid_payload_maps_to_invalid_data(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(**_kwargs: Any) -> _Frame:
        return _Frame([{"日期": date(2021, 10, 28), "收盘": None}])

    monkeypatch.setattr(market_live, "_import_akshare", lambda: _FakeAkshare(broken))
    source = market_live.AkshareMarketDataSource(_settings(), _clock())
    with pytest.raises(ApiError) as caught:
        await source.get_daily_bars(["600519"], date(2021, 10, 1), date(2021, 10, 31))
    assert caught.value.code is ErrorCode.INTERNAL_ERROR
    assert caught.value.metadata[market_live.FAILURE_KEY] == market_live.FAILURE_INVALID_DATA


async def test_empty_frame_returns_empty_sequence(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(**_kwargs: Any) -> _Frame:
        return _Frame([])

    source = _source(handler, monkeypatch)
    bars = await source.get_daily_bars(["600519"], date(2021, 10, 1), date(2021, 10, 31))
    assert bars == ()
    assert source.as_of == TODAY


async def test_unsupported_symbol_does_not_call_akshare(monkeypatch: pytest.MonkeyPatch) -> None:
    source = _source(_noop_handler, monkeypatch)
    with pytest.raises(ApiError) as caught:
        await source.get_daily_bars(["113001"], date(2021, 10, 1), date(2021, 10, 31))
    assert caught.value.code is ErrorCode.NOT_FOUND


async def test_argument_validation(monkeypatch: pytest.MonkeyPatch) -> None:
    source = _source(_noop_handler, monkeypatch)
    with pytest.raises(ApiError) as start_after_end:
        await source.get_daily_bars(["600519"], date(2021, 10, 31), date(2021, 10, 1))
    assert start_after_end.value.code is ErrorCode.VALIDATION_ERROR
    # start 在未来 → 不请求上游，直接空
    future_start = date(2027, 1, 1)
    assert await source.get_daily_bars(["600519"], future_start, future_start) == ()


async def test_latest_quotes_uses_last_bar(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(**kwargs: Any) -> _Frame:
        if kwargs["symbol"] == "601688":
            return _Frame([])
        return _Frame(_records((date(2021, 10, 28), 10.0), (date(2021, 10, 29), 10.5)))

    def empty_fallback(**_kwargs: Any) -> _Frame:
        # 新浪/腾讯调用带 sh/sz 前缀，绕开 handler 的 "601688" 匹配；显式返回空
        return _Frame([])

    source = _source(handler, monkeypatch, sina_handler=empty_fallback, tx_handler=empty_fallback)
    quotes = await source.get_latest_quotes(["600519", "601688"])
    assert len(quotes) == 1
    assert quotes[0].symbol == "600519"
    assert quotes[0].as_of == date(2021, 10, 29)
    assert quotes[0].close_units == 10_500_000


# ── 基金路由 / 退市股兜底 ─────────────────────────────────
async def test_fund_routes_to_fund_interfaces(monkeypatch: pytest.MonkeyPatch) -> None:
    """基金标的走 fund_etf_hist_em（中文列），不调用股票接口。"""

    def fund_em(**kwargs: Any) -> _Frame:
        assert kwargs["symbol"] == "510300"
        assert kwargs["adjust"] == ""
        assert kwargs["start_date"] == "20211001"
        assert kwargs["end_date"] == "20211031"
        return _Frame(_records((date(2021, 10, 28), 4.0), (date(2021, 10, 29), 4.1)))

    source = _source(
        _noop_handler,
        monkeypatch,
        fund_em_handler=fund_em,
        fund_sina_handler=_noop_handler,
    )
    bars = await source.get_daily_bars(["510300"], date(2021, 10, 1), date(2021, 10, 31))
    assert [bar.trade_date for bar in bars] == [date(2021, 10, 28), date(2021, 10, 29)]
    assert bars[0].close_units == 4_000_000
    assert bars[1].return_units == 2_500_000  # +2.5%


async def test_fund_sina_fallback_filters_full_history(monkeypatch: pytest.MonkeyPatch) -> None:
    """东财基金瞬时故障 → 新浪基金全量返回 → 本地按窗口过滤，returns 在全序列上派生。"""

    def fund_sina(**kwargs: Any) -> _Frame:
        assert kwargs["symbol"] == "sh510300"
        return _Frame(
            [
                {"date": date(2021, 10, 27), "close": 4.0},
                {"date": date(2021, 10, 28), "close": 4.1},
                {"date": date(2021, 10, 29), "close": 4.0},
                {"date": date(2021, 11, 1), "close": 4.2},
            ]
        )

    source = _source(
        _noop_handler,
        monkeypatch,
        fund_em_handler=_sina_fallback_transient,
        fund_sina_handler=fund_sina,
        market_live_retry_attempts=1,
    )
    bars = await source.get_daily_bars(["510300"], date(2021, 10, 28), date(2021, 10, 29))
    assert [bar.trade_date for bar in bars] == [date(2021, 10, 28), date(2021, 10, 29)]
    # 窗口首根保留全序列派生的真实收益：4.0→4.1 = +2.5%
    assert bars[0].return_units == 2_500_000
    assert bars[1].return_units == -2_439_024  # 4.1→4.0 = -2.439024%（ROUND_HALF_UP）


async def test_delisted_stock_falls_back_to_tx(monkeypatch: pytest.MonkeyPatch) -> None:
    """退市股：东财/新浪均瞬时故障（新浪对退市股返回空响应）→ 腾讯兜底成功。"""

    def tx(**kwargs: Any) -> _Frame:
        assert kwargs["symbol"] == "sz000418"
        assert kwargs["adjust"] == ""
        assert kwargs["timeout"] == 10.0
        return _Frame(
            [
                {"date": date(2019, 5, 6), "close": 50.0},
                {"date": date(2019, 5, 7), "close": 51.0},
            ]
        )

    source = _source(
        _sina_fallback_transient,
        monkeypatch,
        sina_handler=_sina_fallback_transient,
        tx_handler=tx,
        market_live_retry_attempts=1,
    )
    bars = await source.get_daily_bars(["000418"], date(2019, 5, 1), date(2019, 5, 31))
    assert [bar.trade_date for bar in bars] == [date(2019, 5, 6), date(2019, 5, 7)]
    assert bars[1].return_units == 2_000_000  # +2%


async def test_tx_unknown_symbol_maps_to_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    """腾讯对「查无此票」抛 IndexError → 映射为空结果（走负缓存，不判 invalid_data）。"""

    def empty(**_kwargs: Any) -> _Frame:
        return _Frame([])

    def tx_index_error(**_kwargs: Any) -> _Frame:
        raise IndexError("list index out of range")

    source = _source(
        empty,
        monkeypatch,
        sina_handler=empty,
        tx_handler=tx_index_error,
        market_live_retry_attempts=1,
    )
    bars = await source.get_daily_bars(["600519"], date(2021, 10, 1), date(2021, 10, 31))
    assert bars == ()


async def test_empty_result_cross_validates_next_source(monkeypatch: pytest.MonkeyPatch) -> None:
    """单源空结果不立即返回：继续交叉验证下一源（东财空、腾讯有数 → 用腾讯数据）。"""

    def empty(**_kwargs: Any) -> _Frame:
        return _Frame([])

    def tx(**_kwargs: Any) -> _Frame:
        return _Frame([{"date": date(2021, 10, 28), "close": 10.0}])

    source = _source(
        empty,
        monkeypatch,
        sina_handler=empty,
        tx_handler=tx,
        market_live_retry_attempts=1,
    )
    bars = await source.get_daily_bars(["600519"], date(2021, 10, 1), date(2021, 10, 31))
    assert [bar.trade_date for bar in bars] == [date(2021, 10, 28)]
