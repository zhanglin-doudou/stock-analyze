"""独立 oracle：selection 域 BHAR 朴素实现（§8.5）。

与生产的差异点（刻意独立推导）：
- 个股腿不用 ``ch / c0`` 望远镜恒等，而是**逐日连乘**：对窗口内每个基准日取
  ``close_on_or_before``，逐日 ``(c_t / c_{t-1})`` 相乘；数学上等于 ``c_end / c_start``，
  但实现路径完全不同；
- 基准腿同样逐日连乘 ``(1 + r_m)``；
- 聚类 t 用 Decimal 独立展开（按事件日分 cluster 中心化）。

只 import 标准库；入参为鸭子类型 ctx。value 为自然单位比例。
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal, localcontext
from fractions import Fraction
from typing import Any

from .naive_metrics import (
    RATIO_SCALE,
    OracleResult,
    benchmark_rows,
    close_ladders,
    close_on_or_before,
    period_trades,
    round_units,
)

_ONE = 10**RATIO_SCALE


@dataclass(frozen=True)
class BHarEvent:
    trade_id: str
    security_code: str
    event_date: date
    bench_index: int
    bhar_units: int
    bench_cum_units: int


@dataclass(frozen=True)
class BHarEvents:
    events: tuple[BHarEvent, ...] = ()
    n_events: int = 0
    n_pending: int = 0
    n_excluded_suspended: int = 0
    n_excluded_delisted: int = 0
    n_excluded_noprice: int = 0
    benchmark_id: str | None = None
    available: bool = True

    @property
    def n_valid(self) -> int:
        return len(self.events)


def _decimal_return(units: int) -> Decimal:
    return Decimal(units).scaleb(-RATIO_SCALE)


def _compound(returns: list[int]) -> int:
    """逐日连乘 (1+r)，返回 Π(1+r) − 1 的 scale=8 标度整数（TWR）。"""
    with localcontext() as lc:
        lc.prec = 50
        product = Decimal(1)
        for item in returns:
            product *= Decimal(1) + _decimal_return(item)
        return int(
            (product - Decimal(1)).scaleb(RATIO_SCALE).quantize(Decimal(1), rounding=ROUND_HALF_UP)
        )


def _stock_leg(ladders: dict[str, Any], symbol: str, days: list[date]) -> int | None:
    """逐日连乘 close_t/close_{t-1}；任一价格缺失 → None。"""
    with localcontext() as lc:
        lc.prec = 50
        product = Decimal(1)
        previous = close_on_or_before(ladders, symbol, days[0])
        if previous is None or previous == 0:
            return None
        for day in days[1:]:
            current = close_on_or_before(ladders, symbol, day)
            if current is None:
                return None
            product *= Decimal(current) / Decimal(previous)
            previous = current
        return int(product.scaleb(RATIO_SCALE).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def compute_events(ctx: Any, *, horizon: int) -> BHarEvents:
    bench = benchmark_rows(ctx)
    if ctx.benchmark is None or not bench:
        return BHarEvents(benchmark_id=ctx.benchmark, available=False)

    bench_dates = [item.trade_date for item in bench]
    bench_return = {item.trade_date: item.return_units for item in bench}
    ladders = close_ladders(ctx)
    bar_dates = {symbol: ladder[0] for symbol, ladder in ladders.items()}

    events: list[BHarEvent] = []
    n_pending = 0
    n_suspended = 0
    n_delisted = 0
    n_noprice = 0

    buys = [item for item in period_trades(ctx) if item.category == "buy"]
    for trade in buys:
        j0 = bisect_right(bench_dates, trade.trade_date) - 1
        if j0 < 0:
            n_pending += 1
            continue
        j1 = j0 + horizon
        if j1 >= len(bench_dates) or bench_dates[j1] > ctx.as_of:
            n_pending += 1
            continue

        symbol_dates = bar_dates.get(trade.security_code, ())
        start, end = bench_dates[j0], bench_dates[j1]
        lower = bisect_right(symbol_dates, start)
        upper = bisect_right(symbol_dates, end)
        coverage = upper - lower
        if coverage * 2 < horizon:
            n_suspended += 1
            continue
        tail_start = bench_dates[max(j1 - 4, j0 + 1)]
        tail_lower = bisect_left(symbol_dates, tail_start)
        tail_has_bar = upper - tail_lower > 0
        if not tail_has_bar and coverage * 10 < 9 * horizon:
            n_delisted += 1
            continue

        window = bench_dates[j0 : j1 + 1]
        stock_leg = _stock_leg(ladders, trade.security_code, window)
        bench_cum = _compound([bench_return[day] for day in bench_dates[j0 + 1 : j1 + 1]])
        if stock_leg is None:
            n_noprice += 1
            continue
        events.append(
            BHarEvent(
                trade_id=trade.id,
                security_code=trade.security_code,
                event_date=trade.trade_date,
                bench_index=j0,
                bhar_units=stock_leg - (_ONE + bench_cum),
                bench_cum_units=bench_cum,
            )
        )

    return BHarEvents(
        events=tuple(events),
        n_events=len(buys),
        n_pending=n_pending,
        n_excluded_suspended=n_suspended,
        n_excluded_delisted=n_delisted,
        n_excluded_noprice=n_noprice,
        benchmark_id=ctx.benchmark,
    )


def _median(values: list[int]) -> int:
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2 == 1:
        return ordered[middle]
    return round_units(Fraction(ordered[middle - 1] + ordered[middle], 2), 0)


def _q4(value: Decimal) -> int:
    return int(value.scaleb(4).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def _cluster_t(events: tuple[BHarEvent, ...]) -> int | None:
    n = len(events)
    if n == 0:
        return None
    with localcontext() as lc:
        lc.prec = 50
        mean = sum(_decimal_return(item.bhar_units) for item in events) / n
        clusters: dict[int, list[Decimal]] = {}
        for item in events:
            clusters.setdefault(item.bench_index, []).append(
                _decimal_return(item.bhar_units) - mean
            )
        groups = len(clusters)
        if groups < 2:
            return None
        spread = sum(sum(values) ** 2 for values in clusters.values())
        if spread == 0:
            return 0
        t_value = mean * n * (Decimal(groups - 1) / (Decimal(groups) * spread)).sqrt()
        return _q4(t_value)


def _decomposition(result: BHarEvents) -> dict[str, int | str | None]:
    units = [item.bhar_units for item in result.events]
    return {
        "median": _median(units) if units else None,
        "t": _cluster_t(result.events),
        "hit_rate": (
            round_units(Fraction(sum(1 for value in units if value > 0), len(units)), RATIO_SCALE)
            if units
            else None
        ),
        "n": result.n_valid,
        "n_pending": result.n_pending,
        "n_excluded_suspended": result.n_excluded_suspended,
        "n_excluded_delisted": result.n_excluded_delisted,
        "n_excluded_noprice": result.n_excluded_noprice,
        "mean_benchmark_return": (
            round_units(
                Fraction(sum(item.bench_cum_units for item in result.events), result.n_valid),
                0,
            )
            if result.n_valid
            else None
        ),
    }


def bhar_mean(ctx: Any, *, horizon: int) -> OracleResult:
    result = compute_events(ctx, horizon=horizon)
    if not result.available:
        return OracleResult(None, 0, {"benchmark_id": result.benchmark_id, "n_pending": 0})
    decomposition = _decomposition(result)
    if result.n_valid == 0:
        return OracleResult(None, 0, decomposition)
    units = [item.bhar_units for item in result.events]
    value = Fraction(sum(units), len(units) * _ONE)
    return OracleResult(value, result.n_valid, decomposition)


def bhar_20(ctx: Any) -> OracleResult:
    return bhar_mean(ctx, horizon=20)


def bhar_60(ctx: Any) -> OracleResult:
    return bhar_mean(ctx, horizon=60)


def bhar_hit(ctx: Any) -> OracleResult:
    result = compute_events(ctx, horizon=20)
    if not result.available:
        return OracleResult(None, 0, {"benchmark_id": result.benchmark_id, "n_pending": 0})
    units = [item.bhar_units for item in result.events]
    hits = sum(1 for value in units if value > 0)
    decomposition: dict[str, int | str | None] = {
        "n_hit": hits,
        "mean": (round_units(Fraction(sum(units), len(units)), 0) if units else None),
        "median": _median(units) if units else None,
        "n_pending": result.n_pending,
        "n_excluded_suspended": result.n_excluded_suspended,
        "n_excluded_delisted": result.n_excluded_delisted,
        "n_excluded_noprice": result.n_excluded_noprice,
    }
    value = Fraction(hits, len(units)) if units else None
    return OracleResult(value, len(units), decomposition)


__all__ = ["BHarEvent", "BHarEvents", "bhar_20", "bhar_60", "bhar_hit", "compute_events"]
