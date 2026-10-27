"""MetricContext 组装与 period 解析（Period2-05/06）。

period 约定（C-34）：
- `period=YYYY-MM` 是**闭区间**（月初 → 月末）；
- `from/to` 是**半开区间** `[from, to)`，M1 只接受「月初 → 次月初」且两端同时给；
  二者含义等价时归一成同一个 `YYYY-MM`。
- 未来月 → `INVALID_PERIOD`；period 与 from/to 同传 → `VALIDATION_ERROR`。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

from stock_contracts.errors import ApiError, ErrorCode

from stock_review.metrics.views import (
    AssetBuildView,
    AssetDailyView,
    BarView,
    BenchmarkView,
    CalendarView,
    FillView,
    RoundView,
    TradeView,
)

PeriodLabel = str


@dataclass(frozen=True, slots=True)
class Period:
    label: str
    start: date
    end: date  # 含端点（月末最后一天）


@dataclass(frozen=True, slots=True)
class UpstreamInfo:
    status: str
    inputs_hash: str


@dataclass(frozen=True, slots=True)
class MetricContextData:
    """`MetricContext` Protocol 的冻结实现（§4.3 字段）。"""

    user_id: uuid.UUID
    period: str
    period_start: date
    period_end: date
    as_of: date
    params: dict[str, Any]
    benchmark: str | None
    rf_annual: Decimal
    rounds: tuple[RoundView, ...]
    fills: tuple[FillView, ...]
    trades: tuple[TradeView, ...]
    asset_daily: tuple[AssetDailyView, ...]
    asset_build: AssetBuildView
    bars: tuple[BarView, ...]
    benchmark_series: tuple[BenchmarkView, ...]
    calendar: CalendarView
    flags: dict[str, bool] = field(default_factory=dict)
    upstream: dict[str, UpstreamInfo] = field(default_factory=dict)
    fingerprints: dict[str, str] = field(default_factory=dict)
    market_as_of: date | None = None
    engine_version: str = "1"
    observed_trading_days: int = 0


def _month_start(value: date) -> date:
    return value.replace(day=1)


def _next_month(value: date) -> date:
    if value.month == 12:
        return date(value.year + 1, 1, 1)
    return date(value.year, value.month + 1, 1)


def _month_end(value: date) -> date:
    nxt = _next_month(_month_start(value))
    return date.fromordinal(nxt.toordinal() - 1)


def parse_period_label(raw: str, *, today: date) -> Period:
    try:
        year_text, month_text = raw.split("-")
        year, month = int(year_text), int(month_text)
        start = date(year, month, 1)
    except (ValueError, AttributeError) as exc:
        raise ApiError(
            ErrorCode.INVALID_PERIOD,
            detail=f"period 必须是 YYYY-MM，实际 {raw!r}",
        ) from exc
    if len(year_text) != 4 or len(month_text) != 2:
        raise ApiError(ErrorCode.INVALID_PERIOD, detail=f"period 必须是 YYYY-MM，实际 {raw!r}")
    if start > _month_start(today):
        raise ApiError(ErrorCode.INVALID_PERIOD, detail=f"不能查询未来月份: {raw}")
    return Period(label=f"{year:04d}-{month:02d}", start=start, end=_month_end(start))


def resolve_period(
    *,
    period: str | None,
    date_from: date | None,
    date_to: date | None,
    today: date,
) -> Period:
    """解析 period 或 from/to；缺省 = 当月（前端省略时的稳定默认）。"""
    if period is not None and (date_from is not None or date_to is not None):
        raise ApiError(
            ErrorCode.VALIDATION_ERROR,
            detail="period 与 from/to 只能二选一",
        )
    if period is None and date_from is None and date_to is None:
        return parse_period_label(_month_start(today).isoformat()[:7], today=today)
    if period is not None:
        return parse_period_label(period, today=today)
    if date_from is None or date_to is None:
        raise ApiError(ErrorCode.INVALID_PERIOD, detail="from/to 必须同时提供（半开区间）")
    if date_from > date_to:
        raise ApiError(ErrorCode.INVALID_PERIOD, detail="from 必须不晚于 to")
    if date_from != _month_start(date_from):
        raise ApiError(ErrorCode.INVALID_PERIOD, detail="from 必须是月初（半开区间 [from, to)）")
    if date_to != _next_month(date_from):
        raise ApiError(
            ErrorCode.INVALID_PERIOD,
            detail="M1 的 from/to 只支持同一自然月（to 必须是次月初）",
        )
    if date_from > _month_start(today):
        raise ApiError(ErrorCode.INVALID_PERIOD, detail=f"不能查询未来月份: {date_from}")
    return Period(
        label=date_from.isoformat()[:7],
        start=date_from,
        end=date.fromordinal(date_to.toordinal() - 1),
    )


__all__ = [
    "MetricContextData",
    "Period",
    "UpstreamInfo",
    "parse_period_label",
    "resolve_period",
]
