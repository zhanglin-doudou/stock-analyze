"""指标引擎测试工厂：构造冻结视图与 MetricContextData（零 I/O）。"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from stock_review.metrics.context import MetricContextData
from stock_review.metrics.views import (
    AssetDailyView,
    BarView,
    BenchmarkView,
    FillView,
    RoundView,
    TradeView,
    empty_viewset,
)

PERIOD_START = date(2026, 6, 1)
PERIOD_END = date(2026, 6, 30)
DEFAULT_RF = Decimal("0.015")
USER_ID = uuid.UUID("11111111-1111-1111-1111-111111111111")


def make_round(
    index: int,
    *,
    return_rate_units: int = 10_000_000,
    status: str = "closed",
    holding_days: int = 5,
    close_date: date | None = date(2026, 6, 10),
) -> RoundView:
    return RoundView(
        id=f"rnd_{index:04d}",
        security_code="600519",
        status=status,
        open_date=date(2026, 6, 1),
        close_date=close_date,
        quantity_units=100_0000,
        cost_units=1_000_000_000,
        proceeds_units=1_100_000_000,
        dividend_units=0,
        pnl_units=100_000_000,
        return_rate_units=return_rate_units,
        holding_days=holding_days,
        data_error=False,
        oversell_units=0,
        rebuilt_at=datetime(2026, 6, 30, 15, 0, tzinfo=UTC),
    )


def make_rounds(*, wins: int = 7, losses: int = 5, holding_days: int = 5) -> tuple[RoundView, ...]:
    rounds = [
        make_round(index, return_rate_units=10_000_000, holding_days=holding_days)
        for index in range(wins)
    ]
    rounds += [
        make_round(100 + index, return_rate_units=-5_000_000, holding_days=holding_days)
        for index in range(losses)
    ]
    return tuple(rounds)


def make_context(
    *,
    rounds: tuple[RoundView, ...] = (),
    flags: dict[str, bool] | None = None,
    observed_trading_days: int = 20,
    as_of: date = PERIOD_END,
    benchmark: str | None = None,
    rf_annual: Decimal = DEFAULT_RF,
    params: dict[str, Any] | None = None,
) -> MetricContextData:
    views = empty_viewset(period_start=PERIOD_START, period_end=PERIOD_END)
    return MetricContextData(
        user_id=USER_ID,
        period="2026-06",
        period_start=PERIOD_START,
        period_end=PERIOD_END,
        as_of=as_of,
        params=dict(params or {}),
        benchmark=benchmark,
        rf_annual=rf_annual,
        rounds=tuple(rounds),
        fills=(),
        trades=(),
        asset_daily=(),
        asset_build=views.asset_build,
        bars=(),
        benchmark_series=(),
        calendar=views.calendar,
        flags=dict(flags or {}),
        upstream={},
        fingerprints={"rounds": "sha256:" + "a" * 64},
        market_as_of=None,
        engine_version="1.0",
        observed_trading_days=observed_trading_days,
    )


def make_trade(
    index: int,
    trade_date: date,
    *,
    side: str = "buy",
    category: str = "buy",
    security_code: str = "600519",
    quantity_units: int = 100_0000,
    price_units: int = 1000_0000,
    amount_units: int = -10_0000,
    commission_units: int = 0,
    stamp_tax_units: int = 0,
    other_fee_units: int = 0,
) -> TradeView:
    return TradeView(
        id=f"trd_{index:04d}",
        trade_date=trade_date,
        line_no=index,
        side=side,
        category=category,
        security_code=security_code,
        quantity_units=quantity_units,
        price_units=price_units,
        amount_units=amount_units,
        commission_units=commission_units,
        stamp_tax_units=stamp_tax_units,
        other_fee_units=other_fee_units,
        cash_balance_units=None,
        deleted_at=None,
        version=1,
    )


def make_asset_day(
    trade_date: date,
    *,
    market_value_units: int = 1_000_000,
    cash_units: int = 500_000,
    total_assets_units: int = 1_500_000,
    daily_return_units: int | None = 0,
    status: str = "ok",
) -> AssetDailyView:
    return AssetDailyView(
        trade_date=trade_date,
        cash_units=cash_units,
        market_value_units=market_value_units,
        total_assets_units=total_assets_units,
        holding_cost_units=0,
        unrealized_pnl_units=0,
        holding_count=1,
        cashflow_units=0,
        prev_total_assets_units=None,
        daily_pnl_units=None,
        daily_return_units=daily_return_units,
        status=status,
        definition_version="1.0",
    )


def make_bar(
    trade_date: date,
    *,
    security_code: str = "600519",
    close_units: int = 1000_0000,
    return_units: int = 0,
) -> BarView:
    return BarView(
        symbol=security_code,
        trade_date=trade_date,
        close_units=close_units,
        return_units=return_units,
    )


def make_benchmark(
    trade_date: date, *, return_units: int = 0, available: bool = True
) -> BenchmarkView:
    return BenchmarkView(
        trade_date=trade_date,
        return_units=return_units,
        available=available,
        provenance="test",
    )


def make_fill(
    round_id: str,
    *,
    buy_cost_units: int = 100,
    holding_days: int = 5,
) -> FillView:
    return FillView(
        round_id=round_id,
        sequence=1,
        buy_trade_id=f"{round_id}_b",
        sell_trade_id=f"{round_id}_s",
        quantity_units=100_0000,
        buy_cost_units=buy_cost_units,
        sell_proceeds_units=buy_cost_units,
        dividend_units=0,
        pnl_units=0,
        open_date=PERIOD_START,
        close_date=date(2026, 6, 10),
        holding_days=holding_days,
    )


__all__ = [
    "DEFAULT_RF",
    "PERIOD_END",
    "PERIOD_START",
    "USER_ID",
    "make_asset_day",
    "make_bar",
    "make_benchmark",
    "make_context",
    "make_fill",
    "make_round",
    "make_rounds",
    "make_trade",
]
