"""MetricContext 组装与 period 解析（Period2-05/06）。

period 约定（C-34）：
- `period=YYYY-MM` 是**闭区间**（月初 → 月末）；
- `period=YYYY` 是**整年闭区间**（1/1 → 12/31），用于低频投资者的年度指标；
- `period=YYYY-MM-DD..YYYY-MM-DD` 是**自定义闭区间**（跨度 ≤ 366 天，不落库缓存）；
- `from/to` 是**半开区间** `[from, to)`，只支持「月初 → 次月初」且两端同时给，
  二者含义等价时归一成同一个 `YYYY-MM`。
- 未来月/未来年/未来起点 → `INVALID_PERIOD`；period 与 from/to 同传 → `VALIDATION_ERROR`。
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
PeriodKind = str  # "month" | "year" | "custom"

_CUSTOM_SEP = ".."
CUSTOM_MAX_DAYS = 366


@dataclass(frozen=True, slots=True)
class Period:
    label: str
    start: date
    end: date  # 含端点（月末最后一天 / 12-31 / 自定义终点）

    kind: PeriodKind = "month"

    @property
    def cacheable(self) -> bool:
        """月度/年度可落 `metric_result`；自定义区间无界，不缓存。"""
        return self.kind != "custom"


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
    period_kind: PeriodKind = "month"
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
    """解析 `YYYY-MM` / `YYYY` / `YYYY-MM-DD..YYYY-MM-DD` 三种 period token。"""
    text = raw.strip() if isinstance(raw, str) else ""
    if _CUSTOM_SEP in text:
        return _parse_custom_range(text, today=today)
    if len(text) == 4 and text.isdigit():
        return _parse_year_label(text, today=today)
    try:
        year_text, month_text = text.split("-")
        year, month = int(year_text), int(month_text)
        start = date(year, month, 1)
    except (ValueError, AttributeError) as exc:
        raise ApiError(
            ErrorCode.INVALID_PERIOD,
            detail=f"period 必须是 YYYY-MM / YYYY / YYYY-MM-DD..YYYY-MM-DD，实际 {raw!r}",
        ) from exc
    if len(year_text) != 4 or len(month_text) != 2:
        raise ApiError(ErrorCode.INVALID_PERIOD, detail=f"period 必须是 YYYY-MM，实际 {raw!r}")
    if start > _month_start(today):
        raise ApiError(ErrorCode.INVALID_PERIOD, detail=f"不能查询未来月份: {raw}")
    return Period(label=f"{year:04d}-{month:02d}", start=start, end=_month_end(start))


def _parse_year_label(text: str, *, today: date) -> Period:
    year = int(text)
    if year < 1970:
        raise ApiError(ErrorCode.INVALID_PERIOD, detail=f"period 年份非法: {text}")
    start = date(year, 1, 1)
    if start > _month_start(today):
        raise ApiError(ErrorCode.INVALID_PERIOD, detail=f"不能查询未来年份: {text}")
    return Period(label=f"{year:04d}", start=start, end=date(year, 12, 31), kind="year")


def _parse_iso_date(text: str, *, raw: str) -> date:
    try:
        return date.fromisoformat(text)
    except ValueError as exc:
        raise ApiError(
            ErrorCode.INVALID_PERIOD,
            detail=f"自定义区间两端必须是 YYYY-MM-DD: {raw!r}",
        ) from exc


def _parse_custom_range(text: str, *, today: date) -> Period:
    from_text, _, to_text = text.partition(_CUSTOM_SEP)
    start = _parse_iso_date(from_text.strip(), raw=text)
    end = _parse_iso_date(to_text.strip(), raw=text)
    if start > end:
        raise ApiError(ErrorCode.INVALID_PERIOD, detail=f"自定义区间起点晚于终点: {text}")
    if (end - start).days + 1 > CUSTOM_MAX_DAYS:
        raise ApiError(
            ErrorCode.INVALID_PERIOD,
            detail=f"自定义区间最多 {CUSTOM_MAX_DAYS} 天: {text}",
        )
    if start > today:
        raise ApiError(ErrorCode.INVALID_PERIOD, detail=f"自定义区间不能在未来: {text}")
    return Period(
        label=f"{start.isoformat()}{_CUSTOM_SEP}{end.isoformat()}",
        start=start,
        end=end,
        kind="custom",
    )


def period_is_cacheable(raw: str | None) -> bool:
    """字符串级判定（无需 Clock）：只有自定义区间不落 `metric_result`。"""
    return not (raw and _CUSTOM_SEP in raw)


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
    "CUSTOM_MAX_DAYS",
    "MetricContextData",
    "Period",
    "PeriodKind",
    "UpstreamInfo",
    "parse_period_label",
    "period_is_cacheable",
    "resolve_period",
]
