"""指标引擎测试工厂：构造冻结视图与 MetricContextData（零 I/O）。"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from stock_review.metrics.context import MetricContextData
from stock_review.metrics.views import RoundView, empty_viewset

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


__all__ = [
    "DEFAULT_RF",
    "PERIOD_END",
    "PERIOD_START",
    "USER_ID",
    "make_context",
    "make_round",
    "make_rounds",
]
