"""metrics 数据集装配的纯函数单测（2026-10-30 数据可用性修复）。

覆盖：
- 伪代码/货基/不支持代码过滤 + 可转债发债→上市代码归并（`_metric_symbols`）；
- bars 批量 NOT_FOUND 时逐 symbol 容错（`_bars_for_symbols`）；
- live 基准 ETF 代理 vs replay 原 symbol（`_benchmark_fetch_symbol`）；
- 60 交易日滚动窗口选取（`asset_days_window`）；
- bar_density 只惩罚依赖 bars_daily 的指标（gate）。
"""

from __future__ import annotations

import dataclasses
from datetime import date, timedelta
from types import SimpleNamespace

import pytest
from stock_contracts.errors import ApiError, ErrorCode
from stock_platform.ports import Bar, MarketCapabilities
from stock_review.metrics.datasets import (
    _bars_for_symbols,
    benchmark_fetch_symbol,
    metric_symbols,
    out_of_range_rows,
)
from stock_review.metrics.defs._common import asset_days_window
from stock_review.metrics.gate import decide

from support.metrics_factories import make_asset_day, make_context


def _trade(code: str, name: str = "", mf: int | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        security_code=code,
        security_name=name,
        money_fund_balance_units=mf,
    )


def _round(code: str) -> SimpleNamespace:
    return SimpleNamespace(security_code=code)


# ── 标的过滤 / 归并 ──────────────────────────────────────────


def test_metric_symbols_filters_pseudo_money_fund_unsupported_and_applies_bond_alias() -> None:
    trades = [
        _trade("CASH"),
        _trade("940018", "华泰紫金天天发货币", mf=10_000),
        _trade("830799", "北交所股票"),
        _trade("754019", "曙26发债"),
        _trade("113050", "曙26转债"),
        _trade("600519", "贵州茅台"),
    ]
    rounds = [_round("600519"), _round("CASH")]
    assert metric_symbols(rounds, trades) == ("113050", "600519")


def test_metric_symbols_keeps_unsupported_when_no_classification() -> None:
    # 归一后仍不可分类的代码被剔除（避免一个坏代码让整批 bars 失败）。
    trades = [_trade("830799", "北交所股票"), _trade("600519", "贵州茅台")]
    assert metric_symbols([], trades) == ("600519",)


# ── bars 容错 ───────────────────────────────────────────────


class _FakeMarket:
    def __init__(self, *, mode: str, ok: set[str]) -> None:
        self.capabilities = MarketCapabilities(mode=mode)
        self.ok = ok
        self.batch_calls = 0
        self.single_calls = 0

    async def get_daily_bars(self, symbols, start, end):  # type: ignore[no-untyped-def]
        if len(symbols) > 1:
            self.batch_calls += 1
            raise ApiError(ErrorCode.NOT_FOUND, detail="not supported")
        self.single_calls += 1
        code = symbols[0]
        if code not in self.ok:
            raise ApiError(ErrorCode.NOT_FOUND, detail="not supported")
        return (Bar(symbol=code, trade_date=start, close_units=100, return_units=0),)


@pytest.mark.asyncio
async def test_bars_for_symbols_batch_failure_falls_back_per_symbol() -> None:
    market = _FakeMarket(mode="live", ok={"600519"})
    bars, all_missing, missing = await _bars_for_symbols(
        market, ("600519", "113050"), date(2026, 9, 1), date(2026, 9, 30)
    )
    assert [bar.symbol for bar in bars] == ["600519"]
    assert all_missing is False
    assert missing == ("113050",)
    assert market.batch_calls == 1
    assert market.single_calls == 2


@pytest.mark.asyncio
async def test_bars_for_symbols_all_missing_blocks() -> None:
    market = _FakeMarket(mode="live", ok=set())
    bars, all_missing, missing = await _bars_for_symbols(
        market, ("600519",), date(2026, 9, 1), date(2026, 9, 30)
    )
    assert bars == ()
    assert all_missing is True
    assert missing == ("600519",)


# ── 基准代理 ────────────────────────────────────────────────


def test_benchmark_proxy_only_in_live_mode() -> None:
    assert benchmark_fetch_symbol("hs300_tr", _FakeMarket(mode="live", ok=set())) == "510300"
    assert benchmark_fetch_symbol("hs300_tr", _FakeMarket(mode="replay", ok=set())) == "hs300_tr"


# ── 60 交易日滚动窗口 ───────────────────────────────────────


def test_asset_days_window_takes_last_n_weekdays_including_weekend_rows() -> None:
    end = date(2026, 6, 30)
    start = end - timedelta(days=89)
    rows = tuple(make_asset_day(start + timedelta(days=i)) for i in range(90))
    ctx = dataclasses.replace(make_context(), asset_daily=rows)
    window = asset_days_window(ctx, 60)
    weekdays = [row for row in window if row.trade_date.weekday() < 5]
    assert len(weekdays) == 60
    assert window[0].trade_date <= weekdays[0].trade_date
    assert window[-1].trade_date == rows[-1].trade_date


# ── 门禁：bar_density 不误伤资产日指标 ───────────────────────


def _def(datasets: tuple[str, ...], *, min_trading_days: int = 60) -> SimpleNamespace:
    return SimpleNamespace(
        dependencies=tuple(SimpleNamespace(name=name) for name in datasets),
        sample_policy=SimpleNamespace(min_trading_days=min_trading_days),
        status_policy=(),
    )


def test_bar_density_low_does_not_penalize_asset_only_metric() -> None:
    decision = decide(
        _def(("asset_daily",)),
        make_context(flags={"bar_density_low": True}),
    )
    assert decision.status == "ok"


# ── data_out_of_range 行级判定 ──────────────────────────────


def _round_return(units: int) -> SimpleNamespace:
    return SimpleNamespace(
        security_code="001237",
        open_date=date(2026, 5, 18),
        close_date=date(2026, 5, 22),
        return_rate_units=units,
    )


def _asset_day_return(units: int | None) -> SimpleNamespace:
    return SimpleNamespace(trade_date=date(2026, 10, 30), daily_return_units=units)


def test_out_of_range_allows_legitimate_extreme_round_return() -> None:
    # 新股上市 4 日 +153%（合法极端收益，data_error=false）不得污染整批指标。
    assert out_of_range_rows([_round_return(153_315_096)], []) == ()
    # 恰好 100 倍（1e10）不算越界（严格大于才触发）。
    assert out_of_range_rows([_round_return(10**10)], []) == ()


def test_out_of_range_flags_round_scale_corruption() -> None:
    rows = out_of_range_rows([_round_return(10**10 + 1)], [])
    assert len(rows) == 1
    assert "001237" in rows[0] and "return_rate_units" in rows[0]


def test_out_of_range_flags_daily_return_over_100pct() -> None:
    # 组合单日收益 |r|>100% 基本是桥接/标度错误，维持紧阈值。
    assert out_of_range_rows([], [_asset_day_return(100_000_001)]) != ()
    assert out_of_range_rows([], [_asset_day_return(100_000_000)]) == ()
    assert out_of_range_rows([], [_asset_day_return(None)]) == ()
