"""selection 域 BHAR 事件计算共享件（§3.1 #16/#17/#18，A24/D-26；公式见 §7.4.1）。

发现约定：`registry` 跳过 `_` 前缀模块，本模块不导出 DEF。

口径（D-26）：
- cohort = period 内 `category="buy"` 的成交（权利/打新只入 decomp、本模块不收录）；
- `j0` = 最后一个 `bench_date ≤ buy_date` 的基准日序号，`j1 = j0 + h`；
- 两腿统一锚在 `bench_date[j0]`：个股用 `close_on_or_before`（停牌时滞后，记 c0_lag_days 由
  调用方按需扩展），基准腿用 `return_units` 复利（BenchmarkView 无点位字段，等价望远镜恒等）；
- 剔除：窗口未走完/起点不可锚 → `n_pending`；窗口 bar 覆盖率 <50% → `n_excluded_suspended`；
  尾部 5 个基准日无 bar 且累计覆盖 <90% → `n_excluded_delisted`；事件日无价 → `n_excluded_noprice`。
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from dataclasses import dataclass
from typing import TYPE_CHECKING

from stock_contracts.constants import SCALE_RATIO
from stock_core.metrics.portfolio_index import compound_index
from stock_core.stats.numeric import div_round

from stock_review.metrics.defs._common import (
    close_ladders,
    close_on_or_before,
    period_trades,
    sorted_benchmark,
)

if TYPE_CHECKING:
    import datetime as _dt

    from stock_review.metrics.base import MetricContext

_ONE: int = 10**SCALE_RATIO


@dataclass(frozen=True, slots=True)
class BharEvent:
    trade_id: str
    security_code: str
    event_date: _dt.date
    bench_index: int
    bhar_units: int
    bench_cum_units: int


@dataclass(frozen=True, slots=True)
class BharEvents:
    events: tuple[BharEvent, ...] = ()
    n_events: int = 0
    n_pending: int = 0
    n_excluded_suspended: int = 0
    n_excluded_delisted: int = 0
    n_excluded_noprice: int = 0

    @property
    def n_valid(self) -> int:
        return len(self.events)


def compute_bhar_events(ctx: MetricContext, *, horizon: int) -> BharEvents | None:
    """组装 period 内买入事件的 BHAR 明细；基准缺失返回 None（调用方按 blocked 口径处理）。"""
    benchmark = sorted_benchmark(ctx)
    if ctx.benchmark is None or not benchmark:
        return None
    bench_dates = [row.trade_date for row in benchmark]
    bench_return = {row.trade_date: row.return_units for row in benchmark}

    ladders = close_ladders(ctx.bars)
    bar_dates: dict[str, tuple[_dt.date, ...]] = {
        symbol: ladder[0] for symbol, ladder in ladders.items()
    }

    events: list[BharEvent] = []
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
            n_pending += 1  # 窗口未走完（PIT：前向不得越过 as_of）
            continue

        symbol_dates = bar_dates.get(trade.security_code, ())
        start, end = bench_dates[j0], bench_dates[j1]
        lower = bisect_right(symbol_dates, start)
        upper = bisect_right(symbol_dates, end)
        coverage = upper - lower  # 窗口 (start, end] 内该股 bar 数

        if coverage * 2 < horizon:
            n_suspended += 1  # 停牌代理：覆盖率 <50%（D-26）
            continue
        tail_start = bench_dates[max(j1 - 4, j0 + 1)]
        tail_lower = bisect_left(symbol_dates, tail_start)
        tail_has_bar = upper - tail_lower > 0
        if not tail_has_bar and coverage * 10 < 9 * horizon:
            n_delisted += 1  # 退市代理：尾部 5 日无 bar 且累计覆盖 <90%
            continue

        c0 = close_on_or_before(ladders, trade.security_code, start)
        ch = close_on_or_before(ladders, trade.security_code, end)
        if c0 is None or ch is None or c0 == 0:
            n_noprice += 1
            continue

        leg_returns = [bench_return[day] for day in bench_dates[j0 + 1 : j1 + 1]]
        bench_cum = compound_index(leg_returns)
        stock_leg = div_round(ch, c0, scale=SCALE_RATIO)
        events.append(
            BharEvent(
                trade_id=trade.id,
                security_code=trade.security_code,
                event_date=trade.trade_date,
                bench_index=j0,
                bhar_units=stock_leg - (_ONE + bench_cum),
                bench_cum_units=bench_cum,
            )
        )

    return BharEvents(
        events=tuple(events),
        n_events=len(buys),
        n_pending=n_pending,
        n_excluded_suspended=n_suspended,
        n_excluded_delisted=n_delisted,
        n_excluded_noprice=n_noprice,
    )
