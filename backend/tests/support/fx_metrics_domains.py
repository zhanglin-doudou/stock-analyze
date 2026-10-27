"""S5-2b 域夹具（手工构造，确定性）：fee_breakeven / turnover / averaging_down /
chase / fly / beta / drawdown / hhi 主夹具 / BHAR 20/60。

每份夹具只服务一组手算锚点（见对应 golden 的 ``expected_source``），
数值全部可在设计文档 §8.4 手算表中复核；不复用生产代码生成期望值。
"""

from __future__ import annotations

import dataclasses
import uuid
from datetime import date
from decimal import Decimal

from stock_review.metrics.context import MetricContextData
from stock_review.metrics.views import (
    AssetDailyView,
    BarView,
    BenchmarkView,
    TradeView,
    empty_viewset,
    weekday_days,
)

from support.fx_metrics_core import FINGERPRINTS

USER_ID = uuid.UUID("22222222-2222-2222-2222-222222222222")
RF = Decimal("0.015")

JUNE = weekday_days(date(2026, 6, 1), date(2026, 6, 30))
LONG = weekday_days(date(2026, 6, 1), date(2026, 9, 1))
JUNE_END = date(2026, 6, 30)


def _trade(
    trade_id: str,
    day: date,
    *,
    category: str,
    security: str = "600519",
    qty: int = 1_000_000,
    price: int = 10_000_000,
    amount: int = -100_000,
    commission: int = 0,
) -> TradeView:
    return TradeView(
        id=trade_id,
        trade_date=day,
        line_no=1,
        side=category,
        category=category,
        security_code=security,
        quantity_units=qty,
        price_units=price,
        amount_units=amount,
        commission_units=commission,
        stamp_tax_units=0,
        other_fee_units=0,
        cash_balance_units=None,
        deleted_at=None,
        version=1,
    )


def _asset(
    day: date,
    *,
    mv: int,
    cash: int,
    total: int,
    ret: int | None,
    status: str = "ok",
) -> AssetDailyView:
    return AssetDailyView(
        trade_date=day,
        cash_units=cash,
        market_value_units=mv,
        total_assets_units=total,
        holding_cost_units=0,
        unrealized_pnl_units=0,
        holding_count=1,
        cashflow_units=0,
        prev_total_assets_units=None,
        daily_pnl_units=None,
        daily_return_units=ret,
        status=status,
        definition_version="1.0",
    )


def _bar(symbol: str, day: date, close: int) -> BarView:
    return BarView(symbol=symbol, trade_date=day, close_units=close, return_units=0)


def _bench(day: date, ret: int = 0) -> BenchmarkView:
    return BenchmarkView(trade_date=day, return_units=ret, available=True, provenance="replay")


def _context(
    *,
    period: str,
    period_start: date,
    period_end: date,
    as_of: date,
    observed_trading_days: int,
    trades: tuple[TradeView, ...] = (),
    asset_daily: tuple[AssetDailyView, ...] = (),
    bars: tuple[BarView, ...] = (),
    benchmark: str | None = None,
    benchmark_series: tuple[BenchmarkView, ...] = (),
) -> MetricContextData:
    views = empty_viewset(period_start=period_start, period_end=period_end)
    return MetricContextData(
        user_id=USER_ID,
        period=period,
        period_start=period_start,
        period_end=period_end,
        as_of=as_of,
        params={},
        benchmark=benchmark,
        rf_annual=RF,
        rounds=(),
        fills=(),
        trades=trades,
        asset_daily=asset_daily,
        asset_build=views.asset_build,
        bars=bars,
        benchmark_series=benchmark_series,
        calendar=views.calendar,
        flags={},
        upstream={},
        fingerprints=dict(FINGERPRINTS),
        market_as_of=as_of,
        engine_version="1.0",
        observed_trading_days=observed_trading_days,
    )


# ── perf.fee_breakeven：10 个资产日、佣金 1000 分、D=30 ──


def make_fee_breakeven_context(*, blocked: bool = False) -> MetricContextData:
    rows = tuple(
        _asset(day, mv=1_000_000, cash=0, total=1_000_000, ret=0) for day in JUNE[:10]
    )
    if blocked:
        rows = (
            *rows[:3],
            dataclasses.replace(rows[3], status="blocked"),
            *rows[4:],
        )
    trades = (
        _trade("trd_fee_1", JUNE[1], category="buy", amount=-1_001_000, commission=1000),
    )
    return _context(
        period="2026-06",
        period_start=date(2026, 6, 1),
        period_end=JUNE_END,
        as_of=JUNE_END,
        observed_trading_days=60,
        trades=trades,
        asset_daily=rows,
    )


def make_fee_breakeven_blocked_context() -> MetricContextData:
    return make_fee_breakeven_context(blocked=True)


# ── behavior.turnover：num=200000、avg_mv=1000000、N=10 ──


def make_turnover_context() -> MetricContextData:
    rows = tuple(
        _asset(day, mv=1_000_000, cash=0, total=1_000_000, ret=0) for day in JUNE[:10]
    )
    trades = (
        _trade("trd_turn_1", JUNE[1], category="buy", amount=-120_000),
        _trade("trd_turn_2", JUNE[2], category="sell", amount=80_000),
    )
    return _context(
        period="2026-06",
        period_start=date(2026, 6, 1),
        period_end=JUNE_END,
        as_of=JUNE_END,
        observed_trading_days=60,
        trades=trades,
        asset_daily=rows,
    )


# ── behavior.averaging_down：首建 10.00 → 9.00（摊平）/ 11.00（未摊平）──


def make_multilot_context() -> MetricContextData:
    trades = (
        _trade("trd_avg_0", date(2026, 5, 20), category="buy", price=10_000_000, amount=-100_000),
        _trade("trd_avg_1", date(2026, 6, 5), category="buy", price=9_000_000, amount=-90_000),
        _trade("trd_avg_2", date(2026, 6, 6), category="buy", price=11_000_000, amount=-110_000),
    )
    return _context(
        period="2026-06",
        period_start=date(2026, 6, 1),
        period_end=JUNE_END,
        as_of=JUNE_END,
        observed_trading_days=60,
        trades=trades,
    )


# ── timing.chase：单标的追涨 / 未追涨各 1 笔（20 根窗口）──


def make_bars_chase_context() -> MetricContextData:
    bars = tuple(
        _bar("600519", day, 10_000_000 if index == 0 else 10_400_000)
        for index, day in enumerate(JUNE)
    ) + tuple(_bar("000001", day, 10_200_000) for day in JUNE)
    trades = (
        _trade("trd_chase_1", JUNE[0], category="buy", security="600519", price=10_000_000),
        _trade("trd_chase_2", JUNE[0], category="buy", security="000001", price=10_000_000),
    )
    return _context(
        period="2026-06",
        period_start=date(2026, 6, 1),
        period_end=JUNE_END,
        as_of=JUNE_END,
        observed_trading_days=60,
        trades=trades,
        bars=bars,
    )


# ── timing.fly：卖出后 20/10/5 根 bar 均出现 +4%（>3%）──


def make_bars_fly_context() -> MetricContextData:
    bars = tuple(
        _bar("600519", day, 10_000_000 if index == 0 else 10_400_000)
        for index, day in enumerate(JUNE)
    )
    trades = (
        _trade(
            "trd_fly_1",
            JUNE[0],
            category="sell",
            security="600519",
            price=10_000_000,
            amount=1_000_000,
        ),
    )
    return _context(
        period="2026-06",
        period_start=date(2026, 6, 1),
        period_end=JUNE_END,
        as_of=JUNE_END,
        observed_trading_days=60,
        trades=trades,
        bars=bars,
        benchmark="hs300_tr",
        benchmark_series=tuple(_bench(day) for day in JUNE[:3]),
    )


# ── timing.beta：60 个成对观测，组合收益 == 基准收益（β=1、超额全 0）──


def make_beta_context() -> MetricContextData:
    days = weekday_days(date(2026, 4, 1), JUNE_END)[-60:]
    returns = tuple(1_000_000 if index % 2 == 0 else -1_000_000 for index in range(len(days)))
    rows = tuple(
        _asset(
            day,
            mv=1_000_000,
            cash=0,
            total=1_000_000,
            ret=returns[index],
        )
        for index, day in enumerate(days)
    )
    return _context(
        period="2026-06",
        period_start=date(2026, 6, 1),
        period_end=JUNE_END,
        as_of=JUNE_END,
        observed_trading_days=60,
        asset_daily=rows,
        benchmark="hs300_tr",
        benchmark_series=tuple(
            _bench(day, returns[index]) for index, day in enumerate(days)
        ),
    )


# ── timing.drawdown 小例（§8.4）：10 个日收益、n<60 → pending_window ──

_DD_SMALL_RETURNS = (
    0,
    1_000_000,
    -2_000_000,
    -3_000_000,
    1_000_000,
    -4_000_000,
    2_000_000,
    3_000_000,
    -1_000_000,
    2_000_000,
)


def make_dd_small_context() -> MetricContextData:
    rows = tuple(
        _asset(
            day,
            mv=1_000_000,
            cash=0,
            total=1_000_000,
            ret=_DD_SMALL_RETURNS[index],
        )
        for index, day in enumerate(JUNE[:10])
    )
    return _context(
        period="2026-06",
        period_start=date(2026, 6, 1),
        period_end=JUNE_END,
        as_of=JUNE_END,
        observed_trading_days=10,
        asset_daily=rows,
    )


# ── timing.drawdown 60 日链：+1%×20 / −2%×20 / +1%×20，未恢复 ──


def make_dd_chain_60_context() -> MetricContextData:
    days = weekday_days(date(2026, 4, 1), JUNE_END)[:60]
    returns = (1_000_000,) * 20 + (-2_000_000,) * 20 + (1_000_000,) * 20
    rows = tuple(
        _asset(
            day,
            mv=1_000_000,
            cash=0,
            total=1_000_000,
            ret=returns[index],
        )
        for index, day in enumerate(days)
    )
    return _context(
        period="2026-04..2026-06",
        period_start=date(2026, 4, 1),
        period_end=JUNE_END,
        as_of=JUNE_END,
        observed_trading_days=60,
        asset_daily=rows,
    )


# ── behavior.hhi 主夹具：10 个资产日、单持仓 1000.00 + 现金 1000.00 ──


def make_hhi_main_context() -> MetricContextData:
    trades = (
        _trade(
            "trd_hhi_main",
            date(2026, 5, 29),
            category="buy",
            security="TST001",
            qty=1_000_000,
            price=10_000_000,
            amount=-100_000,
        ),
    )
    bars = tuple(_bar("TST001", day, 10_000_000) for day in JUNE[:10])
    rows = tuple(
        _asset(day, mv=100_000, cash=100_000, total=200_000, ret=None) for day in JUNE[:10]
    )
    return _context(
        period="2026-06",
        period_start=date(2026, 6, 1),
        period_end=JUNE_END,
        as_of=JUNE_END,
        observed_trading_days=60,
        trades=trades,
        asset_daily=rows,
        bars=bars,
    )


# ── selection.bhar_20：三事件 +0.05 / −0.07 / +0.05（基准收益全 0）──

_BHAR20_SYMBOLS = ("AAA", "BBB", "CCC")
_BHAR20_ENDS = (105_000_000, 93_000_000, 105_000_000)


def make_bhar20_context() -> MetricContextData:
    bars: list[BarView] = []
    for offset, (symbol, close) in enumerate(zip(_BHAR20_SYMBOLS, _BHAR20_ENDS, strict=True)):
        bars.extend(_bar(symbol, day, 100_000_000) for day in LONG)
        bars.append(_bar(symbol, LONG[20 + offset], close))
    trades = tuple(
        _trade(
            f"trd_bhar20_{offset}",
            LONG[offset],
            category="buy",
            security=symbol,
            qty=1_000_000,
            price=100_000_000,
            amount=-100_000_000,
        )
        for offset, symbol in enumerate(_BHAR20_SYMBOLS)
    )
    return _context(
        period="2026-06",
        period_start=date(2026, 6, 1),
        period_end=JUNE_END,
        as_of=date(2026, 9, 1),
        observed_trading_days=60,
        trades=trades,
        bars=tuple(bars),
        benchmark="hs300_tr",
        benchmark_series=tuple(_bench(day) for day in LONG),
    )


# ── selection.bhar_60：单事件 100 → 125，基准 +10% → BHAR 0.15 ──


def make_bhar60_context() -> MetricContextData:
    bars = [_bar("DDD", day, 100_000_000) for day in LONG]
    bars.append(_bar("DDD", LONG[60], 125_000_000))
    trades = (
        _trade(
            "trd_bhar60_1",
            LONG[0],
            category="buy",
            security="DDD",
            qty=1_000_000,
            price=100_000_000,
            amount=-100_000_000,
        ),
    )
    bench = tuple(
        _bench(day, 10_000_000 if index == 1 else 0)
        for index, day in enumerate(LONG)
    )
    return _context(
        period="2026-06",
        period_start=date(2026, 6, 1),
        period_end=JUNE_END,
        as_of=date(2026, 9, 1),
        observed_trading_days=60,
        trades=trades,
        bars=tuple(bars),
        benchmark="hs300_tr",
        benchmark_series=bench,
    )


__all__ = [
    "make_bars_chase_context",
    "make_bars_fly_context",
    "make_beta_context",
    "make_bhar20_context",
    "make_bhar60_context",
    "make_dd_chain_60_context",
    "make_dd_small_context",
    "make_fee_breakeven_blocked_context",
    "make_fee_breakeven_context",
    "make_hhi_main_context",
    "make_multilot_context",
    "make_turnover_context",
]
