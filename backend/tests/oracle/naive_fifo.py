"""朴素 FIFO 参考实现（oracle）——独立作者、可读性优先、不考虑性能。

**独立铁律**（Testing §9.3）：
* 不 import `stock_core.*`（生产实现）/ 任何本仓包，只用 Python 标准库；
* 不复制生产代码结构：这里用 `Decimal` 的**自然单位**（股 / 元 / 比例）直算，
  生产实现用标度整数 + 尾差分摊；差异天然暴露「同错同消」的风险。
* 期望值只由本实现产生，测试用它和 `stock_core.fifo.build_rounds` 对拍。

本模块覆盖 FIFO 回合（pnl / 数量 / 成本 / 现金 / 股息）与期末未平仓 lot，
不覆盖持有天数、data_error 的字段细节（那是被测代码的展示口径）。

回合定义（仓位周期）：从 0 股首次买入开始，历经分批买入、做T式部分卖出……
直到持仓回到 0 股才关闭；中途 SELL 只追加 Fill 不关闭。
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
    """一个回合的期望值（自然单位）。

    回合 = 0→非0→0 的仓位周期；中途做T的 SELL 也配对进同一回合。
    """

    key: str
    buy_trade_id: str  # 周期开启时第一笔 BUY
    sell_trade_id: str | None  # 最后一次 SELL；open 周期为 None
    open_date: date
    close_date: date | None
    quantity: Decimal  # 整个周期已 FIFO 配对的股数之和
    cost: Decimal
    proceeds: Decimal
    dividend: Decimal
    pnl: Decimal
    oversell: Decimal = Decimal(0)
    is_open: bool = False


@dataclass(frozen=True)
class OracleResult:
    rounds: tuple[OracleRound, ...]
    open_lots: tuple[OracleLot, ...]


@dataclass
class _RoundState:
    """引擎内部周期聚合。"""

    buy_trade_id: str
    open_date: date
    last_sell_trade_id: str | None = None
    last_sell_date: date | None = None
    matched: Decimal = Decimal(0)
    cost: Decimal = Decimal(0)
    proceeds: Decimal = Decimal(0)
    dividend: Decimal = Decimal(0)
    oversell: Decimal = Decimal(0)


@dataclass
class _State:
    lots: list[OracleLot] = field(default_factory=list)
    rounds: list[OracleRound] = field(default_factory=list)
    current: _RoundState | None = None


def build_rounds(events: Iterable[OracleEvent]) -> OracleResult:
    """朴素 FIFO 配对：逐证券、按稳定序、仓位周期语义。"""
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
        # 期末仍有 lot → open round
        if state.current is not None and state.lots:
            cur = state.current
            pnl = cur.proceeds - cur.cost + cur.dividend
            key = f"{cur.buy_trade_id}:open"
            round_ = OracleRound(
                key=key,
                buy_trade_id=cur.buy_trade_id,
                sell_trade_id=None,
                open_date=cur.open_date,
                close_date=None,
                quantity=cur.matched,
                cost=cur.cost,  # 已消耗 lot 成本，剩余 lot 在 open_lots 独立展示
                proceeds=cur.proceeds,
                dividend=cur.dividend,  # 已消耗 lot 股息，剩余 lot 在 open_lots
                pnl=pnl,
                oversell=cur.oversell,
                is_open=True,
            )
            rounds.append(round_)
        rounds.extend(state.rounds)
        open_lots.extend(state.lots)

    rounds.sort(key=lambda r: (r.close_date or date.max if not r.is_open else date.max, r.key))
    open_lots.sort(key=lambda lot: lot.open_date)
    return OracleResult(rounds=tuple(rounds), open_lots=tuple(open_lots))


def _buy(event: OracleEvent, state: _State) -> None:
    if event.quantity <= 0:
        return
    if state.current is None:
        state.current = _RoundState(buy_trade_id=event.trade_id, open_date=event.trade_date)
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
        # 全超卖 → 无可用 lot，不开启周期
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
    if oversell > 0 and event.quantity > 0:
        # 部分超卖时，把未配对部分的 proceeds 从 proceeds_total 里去掉（生产也这么做）
        proceeds_total = event.amount * matched / event.quantity

    # 维护周期
    if state.current is None:
        first_lot = chunks[0][0]
        state.current = _RoundState(
            buy_trade_id=first_lot.buy_trade_id, open_date=first_lot.open_date
        )
    cur = state.current
    cur.last_sell_trade_id = event.trade_id
    cur.last_sell_date = event.trade_date
    cur.matched += matched
    cur.cost += cost_total
    cur.proceeds += proceeds_total
    cur.dividend += dividend_total
    cur.oversell += oversell

    state.lots = [lot for lot in state.lots if lot.quantity > 0]
    # 持仓归零 → 关闭周期
    if not state.lots:
        pnl = cur.proceeds - cur.cost + cur.dividend
        key = f"{cur.buy_trade_id}:{cur.last_sell_trade_id or 'close'}"
        round_ = OracleRound(
            key=key,
            buy_trade_id=cur.buy_trade_id,
            sell_trade_id=cur.last_sell_trade_id,
            open_date=cur.open_date,
            close_date=cur.last_sell_date,
            quantity=cur.matched,
            cost=cur.cost,
            proceeds=cur.proceeds,
            dividend=cur.dividend,
            pnl=pnl,
            oversell=cur.oversell,
            is_open=False,
        )
        state.rounds.append(round_)
        state.current = None


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
