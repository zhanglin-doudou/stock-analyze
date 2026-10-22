"""朴素 FIFO 参考实现（oracle）——独立作者、可读性优先、不考虑性能。

**独立铁律**（Testing §9.3）：
* 不 import `stock_core.*`（生产实现）/ 任何本仓包，只用 Python 标准库；
* 不复制生产代码结构：这里用 `Decimal` 的**自然单位**（股 / 元 / 比例）直算，
  生产实现用标度整数 + 尾差分摊；差异天然暴露「同错同消」的风险。
* 期望值只由本实现产生，测试用它和 `stock_core.fifo.build_rounds` 对拍。

本模块只覆盖 FIFO 回合（pnl / 数量 / 成本 / 现金 / 股息）与期末未平仓 lot，
不覆盖持有天数、data_error 的字段细节（那是被测代码的展示口径）。
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

_ONE = Decimal(1)


@dataclass(frozen=True)
class OracleEvent:
    """自然单位事件：quantity=股，amount/dividend_tax=元，ratio=比例（0.3 = 10送3）。"""

    trade_id: str
    trade_date: date
    kind: str
    security_code: str = ""
    quantity: Decimal = Decimal(0)
    price: Decimal = Decimal(0)
    amount: Decimal = Decimal(0)
    dividend_tax: Decimal = Decimal(0)
    ratio: Decimal = Decimal(0)
    line_no: int = 0
    summary: str = ""

    @property
    def sort_key(self) -> tuple[date, int, str, Decimal, Decimal, str]:
        """A7 稳定序：交易日 → 文件行号 → 摘要 → 数量 → 均价。"""
        return (
            self.trade_date,
            self.line_no,
            self.summary,
            self.quantity,
            self.price,
            self.trade_id,
        )


@dataclass
class OracleLot:
    """一个未平仓买入 lot（成本/股息可继续被 FIFO 消耗）。"""

    buy_trade_id: str
    open_date: date
    quantity: Decimal
    cost: Decimal
    dividend: Decimal = Decimal(0)


@dataclass(frozen=True)
class OracleRound:
    """一个回合的期望值（自然单位）。"""

    key: str
    buy_trade_id: str
    sell_trade_id: str
    open_date: date
    close_date: date
    quantity: Decimal
    cost: Decimal
    proceeds: Decimal
    dividend: Decimal
    pnl: Decimal
    oversell: Decimal = Decimal(0)


@dataclass(frozen=True)
class OracleResult:
    rounds: tuple[OracleRound, ...]
    open_lots: tuple[OracleLot, ...]


@dataclass
class _State:
    lots: list[OracleLot] = field(default_factory=list)
    rounds: list[OracleRound] = field(default_factory=list)


def build_rounds(events: Iterable[OracleEvent]) -> OracleResult:
    """朴素 FIFO 配对：逐证券、按稳定序、持有 lot 列表。"""
    ordered = sorted(events, key=lambda event: event.sort_key)
    by_security: dict[str, list[OracleEvent]] = {}
    for event in ordered:
        by_security.setdefault(event.security_code, []).append(event)

    rounds: list[OracleRound] = []
    open_lots: list[OracleLot] = []
    for security_code in sorted(by_security):
        state = _State()
        for event in by_security[security_code]:
            if event.kind == "buy":
                _buy(event, state)
            elif event.kind == "sell":
                _sell(event, state)
            elif event.kind == "dividend":
                _dividend(event, state)
            elif event.kind == "bonus":
                _bonus(event, state)
            elif event.kind == "rights":
                _rights(event, state)
            # 其它事件不参与回合配对
        rounds.extend(state.rounds)
        open_lots.extend(state.lots)

    rounds.sort(key=lambda round_: (round_.close_date, round_.key))
    open_lots.sort(key=lambda lot: lot.open_date)
    return OracleResult(rounds=tuple(rounds), open_lots=tuple(open_lots))


def _buy(event: OracleEvent, state: _State) -> None:
    if event.quantity <= 0:
        return
    state.lots.append(
        OracleLot(
            buy_trade_id=event.trade_id,
            open_date=event.trade_date,
            quantity=event.quantity,
            cost=abs(event.amount),
        )
    )


def _sell(event: OracleEvent, state: _State) -> None:
    if event.quantity <= 0:
        return
    remaining = event.quantity
    chunks: list[tuple[OracleLot, Decimal]] = []
    for lot in state.lots:
        if remaining <= 0:
            break
        take = min(remaining, lot.quantity)
        chunks.append((lot, take))
        remaining -= take
    if not chunks:
        return
    matched = sum((take for _, take in chunks), Decimal(0))
    oversell = remaining
    cost_total = Decimal(0)
    dividend_total = Decimal(0)
    proceeds_total = Decimal(0)
    for lot, take in chunks:
        # 朴素实现：直接按持股比例分摊（生产实现用整数 floor + 尾差，差异 ≤ 1 分）。
        fraction = take / lot.quantity
        cost = lot.cost * fraction
        dividend = lot.dividend * fraction
        lot.quantity -= take
        lot.cost -= cost
        lot.dividend -= dividend
        proceeds = event.amount * take / event.quantity
        cost_total += cost
        dividend_total += dividend
        proceeds_total += proceeds
    first_lot = chunks[0][0]
    state.rounds.append(
        OracleRound(
            key=f"{first_lot.buy_trade_id}:{event.trade_id}",
            buy_trade_id=first_lot.buy_trade_id,
            sell_trade_id=event.trade_id,
            open_date=first_lot.open_date,
            close_date=event.trade_date,
            quantity=matched,
            cost=cost_total,
            proceeds=proceeds_total,
            dividend=dividend_total,
            pnl=proceeds_total - cost_total + dividend_total,
            oversell=oversell,
        )
    )
    state.lots = [lot for lot in state.lots if lot.quantity > 0]


def _dividend(event: OracleEvent, state: _State) -> None:
    if not state.lots:
        return
    net = event.amount - event.dividend_tax
    total_quantity = sum((lot.quantity for lot in state.lots), Decimal(0))
    for lot in state.lots:
        lot.dividend += net * lot.quantity / total_quantity


def _bonus(event: OracleEvent, state: _State) -> None:
    total_quantity = sum((lot.quantity for lot in state.lots), Decimal(0))
    if not state.lots:
        return
    if event.ratio > 0:
        added = (total_quantity * event.ratio).quantize(_ONE, rounding=ROUND_HALF_UP)
    else:
        added = event.quantity
    if added <= 0:
        return
    for lot in state.lots:
        lot.quantity += added * lot.quantity / total_quantity


def _rights(event: OracleEvent, state: _State) -> None:
    if not state.lots:
        return
    if event.quantity <= 0:
        return
    payment = abs(event.amount)
    total_quantity = sum((lot.quantity for lot in state.lots), Decimal(0))
    for lot in state.lots:
        fraction = lot.quantity / total_quantity
        lot.quantity += event.quantity * fraction
        lot.cost += payment * fraction
