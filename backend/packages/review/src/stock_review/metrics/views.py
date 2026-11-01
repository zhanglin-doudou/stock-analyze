"""8 个冻结数据视图（Period2-05，§4.3 冻结字段）。

视图只放**纯数据**：不可变 tuple / frozenset，不暴露 ORM 对象、不带 Session、不做 I/O。
def 的 `compute` 只依赖这些形状；07 新增字段属于 additive，改视图不改引擎。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta


@dataclass(frozen=True, slots=True)
class RoundView:
    id: str
    security_code: str
    status: str
    open_date: date
    close_date: date | None
    quantity_units: int
    cost_units: int
    proceeds_units: int
    dividend_units: int
    pnl_units: int
    return_rate_units: int
    holding_days: int
    data_error: bool
    oversell_units: int
    rebuilt_at: datetime


@dataclass(frozen=True, slots=True)
class FillView:
    round_id: str
    sequence: int
    buy_trade_id: str
    sell_trade_id: str
    quantity_units: int
    buy_cost_units: int
    sell_proceeds_units: int
    dividend_units: int
    pnl_units: int
    open_date: date
    close_date: date
    holding_days: int


@dataclass(frozen=True, slots=True)
class TradeView:
    id: str
    trade_date: date
    line_no: int
    side: str
    category: str
    security_code: str
    quantity_units: int
    price_units: int
    amount_units: int
    commission_units: int
    stamp_tax_units: int
    other_fee_units: int
    cash_balance_units: int | None
    deleted_at: datetime | None
    version: int
    # 视图 additive（2026-10-30）：识别伪代码/货基/发债归并用，不参与旧 compute。
    security_name: str = ""
    money_fund_balance_units: int | None = None


@dataclass(frozen=True, slots=True)
class AssetDailyView:
    trade_date: date
    cash_units: int
    market_value_units: int
    total_assets_units: int
    holding_cost_units: int
    unrealized_pnl_units: int
    holding_count: int
    cashflow_units: int
    prev_total_assets_units: int | None
    daily_pnl_units: int | None
    daily_return_units: int | None
    status: str
    definition_version: str


@dataclass(frozen=True, slots=True)
class AssetBuildView:
    """`asset_build_state` 的冻结快照（`state` ∈ current/stale/blocked/missing）。"""

    state: str
    updated_at: datetime | None
    version: int | None
    last_status: str | None
    data_as_of: date | None
    definition_version: str | None
    last_built_through: date | None


@dataclass(frozen=True, slots=True)
class BarView:
    symbol: str
    trade_date: date
    close_units: int
    return_units: int


@dataclass(frozen=True, slots=True)
class BenchmarkView:
    trade_date: date
    return_units: int
    available: bool
    provenance: str


@dataclass(frozen=True, slots=True)
class CalendarView:
    """weekday_proxy 日历（M1）：只缺节假日，不读系统时间。"""

    assumption: str
    days: tuple[date, ...]
    trading_days: int


@dataclass(frozen=True, slots=True)
class ViewSet:
    """一次请求内共享的 8 视图 + 各自指纹（§6.4）。"""

    rounds: tuple[RoundView, ...] = ()
    fills: tuple[FillView, ...] = ()
    trades: tuple[TradeView, ...] = ()
    asset_daily: tuple[AssetDailyView, ...] = ()
    asset_build: AssetBuildView = field(
        default_factory=lambda: AssetBuildView(
            state="missing",
            updated_at=None,
            version=None,
            last_status=None,
            data_as_of=None,
            definition_version=None,
            last_built_through=None,
        )
    )
    bars: tuple[BarView, ...] = ()
    benchmark: tuple[BenchmarkView, ...] = ()
    calendar: CalendarView = field(
        default_factory=lambda: CalendarView(assumption="weekday_proxy", days=(), trading_days=0)
    )
    fingerprints: Mapping[str, str] = field(default_factory=dict)
    flags: Mapping[str, bool] = field(default_factory=dict)
    market_as_of: date | None = None
    market_provenance: str | None = None
    observed_trading_days: int = 0

    def fingerprint(self, dataset: str) -> str | None:
        return self.fingerprints.get(dataset)


def weekday_days(start: date, end: date) -> tuple[date, ...]:
    """闭区间 `[start, end]` 的工作日序列（Mon–Fri，不含节假日）。"""
    if end < start:
        return ()
    days: list[date] = []
    current = start
    while current <= end:
        if current.weekday() < 5:
            days.append(current)
        current += timedelta(days=1)
    return tuple(days)


def calendar_of(start: date, end: date) -> CalendarView:
    days = weekday_days(start, end)
    return CalendarView(assumption="weekday_proxy", days=days, trading_days=len(days))


def empty_viewset(*, period_start: date, period_end: date) -> ViewSet:
    return ViewSet(calendar=calendar_of(period_start, period_end))


__all__ = [
    "AssetBuildView",
    "AssetDailyView",
    "BarView",
    "BenchmarkView",
    "CalendarView",
    "FillView",
    "RoundView",
    "TradeView",
    "ViewSet",
    "calendar_of",
    "empty_viewset",
    "weekday_days",
]
