"""defs 共享辅助（Period2-07 S4）：回合/成交过滤、费用四桶、价格阶梯。

发现约定：`registry` 跳过 `_` 前缀模块，本模块不导出 DEF，不会被注册为指标。
全部为纯函数：零 I/O、零时钟；金额 scale=2、价格 scale=6、数量 scale=4、比率 scale=8。
"""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict
from stock_contracts.constants import SCALE_AMOUNT, SCALE_PRICE, SCALE_QTY

if TYPE_CHECKING:
    import datetime as _dt

    from stock_review.metrics.base import MetricContext
    from stock_review.metrics.views import (
        AssetDailyView,
        BarView,
        BenchmarkView,
        RoundView,
        TradeView,
    )


class EmptyParams(BaseModel):
    """无参数指标的统一 params schema（registry 强制 extra="forbid"）。"""

    model_config = ConfigDict(extra="forbid")


TRADE_LEGS: frozenset[str] = frozenset({"buy", "sell"})
_PRICE_QTY_TO_MONEY: int = 10 ** (SCALE_QTY + SCALE_PRICE - SCALE_AMOUNT)  # 1e8


def closed_rounds(ctx: MetricContext) -> list[RoundView]:
    """`status=closed` 且 `close_date ∈ period` 的回合；`data_error` 一律剔除（§7.1 域公共口径）。"""
    rows = [
        item
        for item in ctx.rounds
        if item.status == "closed"
        and not item.data_error
        and item.close_date is not None
        and ctx.period_start <= item.close_date <= ctx.period_end
    ]
    return sorted(rows, key=lambda item: (item.close_date or item.open_date, item.id))


def open_rounds(ctx: MetricContext) -> list[RoundView]:
    """`status=open` 且 `open_date ≤ period_end` 的回合（只入全样本/纸面盈亏）。"""
    rows = [
        item
        for item in ctx.rounds
        if item.status == "open" and not item.data_error and item.open_date <= ctx.period_end
    ]
    return sorted(rows, key=lambda item: (item.open_date, item.id))


def data_error_rounds_count(ctx: MetricContext) -> int:
    return sum(1 for item in ctx.rounds if item.data_error)


def period_trades(ctx: MetricContext) -> list[TradeView]:
    """`trade_date ∈ period` 的成交行（排除软删），稳定序 (trade_date, line_no, id)。"""
    rows = [
        item
        for item in ctx.trades
        if item.deleted_at is None and ctx.period_start <= item.trade_date <= ctx.period_end
    ]
    return sorted(rows, key=lambda item: (item.trade_date, item.line_no, item.id))


def trades_until(ctx: MetricContext) -> list[TradeView]:
    """`trade_date ≤ period_end` 的全部成交行（持仓/摊平重建用，含 period 前历史）。"""
    rows = [
        item for item in ctx.trades if item.deleted_at is None and item.trade_date <= ctx.period_end
    ]
    return sorted(rows, key=lambda item: (item.trade_date, item.line_no, item.id))


def money_units(quantity_units: int, price_units: int) -> int:
    """`qty × price` 的毛成交额（scale=2 分，向下取整；与 ledger 估值口径一致）。"""
    return quantity_units * price_units // _PRICE_QTY_TO_MONEY


@dataclass(frozen=True, slots=True)
class FeeBreakdown:
    """A21/D-27 费用四桶：佣金/印花税/其他费 + 独立费用行（三列全 0 的 category=fee 行）。"""

    fee_amount: int
    commission: int
    stamp_tax: int
    other_fee: int
    standalone_fee: int
    turnover_gross: int
    turnover_net: int
    n_fee_rows: int
    n_trade_legs: int


def fee_breakdown(trades: list[TradeView]) -> FeeBreakdown:
    commission = sum(item.commission_units for item in trades)
    stamp_tax = sum(item.stamp_tax_units for item in trades)
    other_fee = sum(item.other_fee_units for item in trades)
    standalone_fee = sum(
        abs(item.amount_units)
        for item in trades
        if item.category == "fee"
        and item.commission_units == 0
        and item.stamp_tax_units == 0
        and item.other_fee_units == 0
    )
    legs = [item for item in trades if item.category in TRADE_LEGS]
    turnover_gross = sum(money_units(item.quantity_units, item.price_units) for item in legs)
    turnover_net = sum(abs(item.amount_units) for item in legs)
    return FeeBreakdown(
        fee_amount=commission + stamp_tax + other_fee + standalone_fee,
        commission=commission,
        stamp_tax=stamp_tax,
        other_fee=other_fee,
        standalone_fee=standalone_fee,
        turnover_gross=turnover_gross,
        turnover_net=turnover_net,
        n_fee_rows=sum(1 for item in trades if item.category == "fee"),
        n_trade_legs=len(legs),
    )


# —— 价格阶梯（close_on_or_before）——

PriceLadder = dict[str, tuple[tuple["_dt.date", ...], tuple[int, ...]]]


def close_ladders(bars: tuple[BarView, ...]) -> PriceLadder:
    """每个 symbol 的 (升序日期, 升序收盘) 阶梯；同日重复 bar 取最后一条。"""
    by_symbol: dict[str, dict[_dt.date, int]] = {}
    for bar in bars:
        by_symbol.setdefault(bar.symbol, {})[bar.trade_date] = bar.close_units
    ladders: PriceLadder = {}
    for symbol, entries in by_symbol.items():
        ordered = sorted(entries.items())
        ladders[symbol] = (
            tuple(day for day, _ in ordered),
            tuple(close for _, close in ordered),
        )
    return ladders


def close_on_or_before(ladders: PriceLadder, symbol: str, day: _dt.date) -> int | None:
    ladder = ladders.get(symbol)
    if ladder is None:
        return None
    dates, closes = ladder
    index = bisect_right(dates, day) - 1
    if index < 0:
        return None
    return closes[index]


def sorted_benchmark(ctx: MetricContext) -> list[BenchmarkView]:
    """可用基准行（available=True），按日期升序；同日重复取最后一条。"""
    by_date: dict[_dt.date, BenchmarkView] = {}
    for row in ctx.benchmark_series:
        if row.available:
            by_date[row.trade_date] = row
    return [by_date[day] for day in sorted(by_date)]


def benchmark_available(ctx: MetricContext) -> bool:
    """`benchmark_required=True` 指标的统一前置：配置 + 可用序列缺一不可。"""
    return ctx.benchmark is not None and bool(sorted_benchmark(ctx))


def asset_days_in_period(ctx: MetricContext) -> list[AssetDailyView]:
    """period 内 asset_daily 行（按日期升序）。"""
    rows = [row for row in ctx.asset_daily if ctx.period_start <= row.trade_date <= ctx.period_end]
    return sorted(rows, key=lambda row: row.trade_date)


def asset_days_window(ctx: MetricContext, trading_days: int) -> list[AssetDailyView]:
    """截至 `period_end` 的回看窗口（含 blocked 行），以最后 N 个工作日为界。

    用于回撤/β 等「期末回看 60 个交易日」的日频指标：`asset_daily` 视图已向前加载
    回看数据，本函数只截最后 N 个交易日对应的自然日区间（周末行保留以保持收益序列
    与原口径一致）。
    """
    rows = sorted(
        (row for row in ctx.asset_daily if row.trade_date <= ctx.period_end),
        key=lambda row: row.trade_date,
    )
    weekdays = [row for row in rows if row.trade_date.weekday() < 5]
    if not weekdays:
        return []
    cutoff_index = max(0, len(weekdays) - trading_days)
    cutoff = weekdays[cutoff_index].trade_date
    return [row for row in rows if row.trade_date >= cutoff]
