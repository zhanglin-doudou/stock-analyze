"""FIFO 回合引擎的公共值对象（Period2-01，冻结接口见 var/period2/INTERFACE.md §2.1）。

全部字段为标度整数（Money 分 / Price 1e-6 / Qty 1e-4 / Ratio 1e-8）；本模块零 I/O、
零 wall-clock。数据类一律 `frozen=True, slots=True`，保证结果不可变、可缓存、可哈希。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from typing import Final


class EventKind(StrEnum):
    """账单事件类型（与 ledger Trade.category 的映射由应用层负责）。"""

    BUY = "buy"
    SELL = "sell"
    DIVIDEND = "dividend"
    BONUS = "bonus"
    RIGHTS = "rights"
    INTEREST = "interest"
    FEE = "fee"
    TRANSFER = "transfer"
    NEW_STOCK = "new_stock"
    OTHER = "other"


@dataclass(frozen=True, slots=True)
class FifoEvent:
    """FIFO 引擎输入事件。

    `amount_units` 约定为**带符号净现金流**：买入为负，卖出/股息/利息为正。
    送转使用 `ratio_units`（如 10 送 3 → 0.3，即 30_000_000）或 `quantity_units`
    （比例缺失时的新增股数）；配股缴款 `amount_units` 为负、`quantity_units` 为新增股数。
    """

    trade_id: str
    trade_date: date
    kind: EventKind
    security_code: str = ""
    security_name: str = ""
    quantity_units: int = 0
    price_units: int = 0
    amount_units: int = 0
    commission_units: int = 0
    stamp_tax_units: int = 0
    other_fee_units: int = 0
    dividend_tax_units: int = 0
    ratio_units: int = 0
    line_no: int = 0
    summary: str = ""

    @property
    def stable_key(self) -> tuple[date, int, str, int, int, str]:
        """稳定序：`(交易日, 文件行号, 摘要, 数量, 均价, trade_id)`（PRD 附录 A7）。"""
        return (
            self.trade_date,
            self.line_no,
            self.summary,
            self.quantity_units,
            self.price_units,
            self.trade_id,
        )


@dataclass(frozen=True, slots=True)
class Fill:
    """一个回合内的一次买入 lot 消耗（同一回合内 sequence 从 1 开始）。"""

    buy_trade_id: str
    sell_trade_id: str
    sequence: int
    quantity_units: int
    buy_cost_units: int
    sell_proceeds_units: int
    dividend_units: int
    pnl_units: int
    open_date: date
    close_date: date
    holding_days: int


@dataclass(frozen=True, slots=True)
class Round:
    """FIFO 回合：closed（正常配对）/ open（期末未平仓）/ data_error（超卖等）。"""

    key: str
    security_code: str
    security_name: str
    status: str
    open_trade_id: str
    close_trade_id: str | None
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
    fills: tuple[Fill, ...]


@dataclass(frozen=True, slots=True)
class OpenLot:
    """期末仍持有的买入 lot（含送转数量与配股缴款成本）。"""

    security_code: str
    buy_trade_id: str
    open_date: date
    quantity_units: int
    cost_units: int
    dividend_units: int


@dataclass(frozen=True, slots=True)
class DataError:
    """无法静默配平的数据异常（绝不产生负持仓）。"""

    trade_id: str
    security_code: str
    trade_date: date
    kind: str
    quantity_units: int
    detail: str


@dataclass(frozen=True, slots=True)
class FifoResult:
    """FIFO 引擎输出。rounds = closed（含 data_error）+ open，按稳定序排列。"""

    rounds: tuple[Round, ...]
    fills: tuple[Fill, ...]
    open_lots: tuple[OpenLot, ...]
    data_errors: tuple[DataError, ...]

    @property
    def closed_rounds(self) -> tuple[Round, ...]:
        """正常平仓回合（status == "closed"；data_error 不计入）。"""
        return tuple(round_ for round_ in self.rounds if round_.status == "closed")

    @property
    def open_rounds(self) -> tuple[Round, ...]:
        return tuple(round_ for round_ in self.rounds if round_.status == "open")

    @property
    def realized_pnl_units(self) -> int:
        """已平仓回合 pnl 之和（含股息）。"""
        return sum(round_.pnl_units for round_ in self.closed_rounds)

    @property
    def trading_pnl_units(self) -> int:
        """已平仓回合交易损益（不含股息）。"""
        return sum(round_.pnl_units - round_.dividend_units for round_ in self.closed_rounds)

    @property
    def dividend_units(self) -> int:
        """已分配股息净额之和（closed + open；data_error 不计入）。"""
        return sum(
            round_.dividend_units
            for round_ in self.rounds
            if round_.status in _DIVIDEND_COUNTED_STATUSES
        )


_DIVIDEND_COUNTED_STATUSES: Final[frozenset[str]] = frozenset({"closed", "open"})
