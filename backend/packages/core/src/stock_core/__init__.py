"""纯计算内核（core）：确定性、零 I/O、零全局态、零 wall-clock。

L1 铁律：`core` 不得 import 任何 I/O 库，不得读系统时钟。
"""

from stock_core.clock import Clock, FrozenClock, SystemClock, require_clock
from stock_core.nav import (
    CashflowPoint,
    NavPoint,
    NavSeries,
    back_solve_initial_cash,
    build_nav_series,
    compute_xirr,
)
from stock_core.values import Money, Price, Qty, Ratio, Scaled

__all__ = [
    "CashflowPoint",
    "Clock",
    "FrozenClock",
    "Money",
    "NavPoint",
    "NavSeries",
    "Price",
    "Qty",
    "Ratio",
    "Scaled",
    "SystemClock",
    "back_solve_initial_cash",
    "build_nav_series",
    "compute_xirr",
    "require_clock",
]
