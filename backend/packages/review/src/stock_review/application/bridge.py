"""对账桥接应用层（API-027）：FIFO 两套成本口径的桥接恒等式。

公式（INTERFACE §2.5 / PRD FR-R2.4）：

    residual = (initial_cash + initial_open_cost + cashflows + trading_pnl
                + dividends + interest - fees + other) - ending_value
    ending_value = ending_cash + open_cost

分类口径（INTERFACE §3.6，本文件实现时的两处必要澄清）：
- `buy/sell` 通过 FIFO 回合进入 `trading_pnl`（区间内 close_date 的 closed 回合
  `pnl - dividend` 之和），期末未平仓 lot 成本进 `open_cost`；
- `dividend` 计净股息、`interest` 计利息、`fee` 计正费用（amount 为负，取反）、
  `transfer` 计外部资金流；
- `new_stock/other/missing/NULL` 等未分类现金流进 `other` 并把状态降级为 `degraded`
  （打新冻结/解冻、对账补差等语义无法拆分，只能整体补差）；
- `rights`（配股缴款）**不进 other**：其缴款额已被 FIFO 计入买入 lot 成本
  （core `RIGHTS` 成本取 `abs(amount_units)`），再计一次会让恒等式双边重复；
- `bonus`（送转）零金额、不产生现金流，同样不参与分类。

现金锚点：
- 期初 = `period_start` 之前最后一条 `cash_balance`；缺失时用首行反推
  `cash_balance - amount`（PRD FR-R2.1 的 `back_solved` 口径）；
- 期末 = 区间内最后一条 `cash_balance`；区间内没有余额行时按
  `期初 + Σ区间现金流` 推导并降级；
- 完全没有 cash_balance（无任何锚点）→ `status="blocked"`，`balanced=False`，`residual=0`。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy.ext.asyncio import AsyncSession
from stock_contracts.clock import Clock
from stock_core.fifo import BridgeIdentity, BridgeLedger, build_rounds, compute_bridge
from stock_ledger.models import Trade
from stock_ledger.repositories.trades import TradeRepository

from stock_review.application.rebuild import trades_to_events

# 桥接容差：1 分（标度整数最小单位的 1 倍；金额全部是分）。
TOLERANCE_UNITS = 1

# 直接进入 FIFO 回合分类的类别（其余类别按 §3.6 单列）。
_ROUND_CATEGORIES: frozenset[str] = frozenset({"buy", "sell"})
# 不参与 bridge 分类的公司行为：rights 已进买入成本、bonus 零现金流（见模块 docstring）。
_NON_CASH_CATEGORIES: frozenset[str] = frozenset({"rights", "bonus"})


@dataclass(slots=True)
class BridgeResult:
    """API-027 返回体（金额全部为标度整数；接口层再转定点字符串）。"""

    period: str
    balanced: bool
    initial_cash_units: int
    initial_open_cost_units: int
    cashflows_units: int
    realized_pnl_units: int
    dividends_units: int
    interest_units: int
    fees_units: int
    other_units: int
    open_cost_units: int
    ending_value_units: int
    residual_units: int
    tolerance_units: int
    status: str
    data_as_of: datetime
    inputs_hash: str


def _category(trade: Trade) -> str:
    return (trade.category or "").strip().lower()


def _is_cash_balance(trade: Trade) -> bool:
    return trade.cash_balance_units is not None


def _blocked(period: str, clock: Clock) -> BridgeResult:
    """无任何现金锚点：桥接不可计算（零输入走 core 公式保证 inputs_hash 确定性）。"""
    identity = compute_bridge(BridgeLedger(initial_cash_units=0), tolerance_units=TOLERANCE_UNITS)
    return _to_result(period, identity, clock=clock, status="blocked", balanced=False)


def _to_result(
    period: str,
    identity: BridgeIdentity,
    *,
    clock: Clock,
    status: str,
    balanced: bool | None = None,
) -> BridgeResult:
    return BridgeResult(
        period=period,
        balanced=identity.balanced if balanced is None else balanced,
        initial_cash_units=identity.initial_cash_units,
        initial_open_cost_units=identity.initial_open_cost_units,
        cashflows_units=identity.cashflows_units,
        realized_pnl_units=identity.trading_pnl_units,
        dividends_units=identity.dividends_units,
        interest_units=identity.interest_units,
        fees_units=identity.fees_units,
        other_units=identity.other_units,
        open_cost_units=identity.open_cost_units,
        ending_value_units=identity.ending_value_units,
        residual_units=identity.residual_units,
        tolerance_units=identity.tolerance_units,
        status=status,
        data_as_of=clock.now(),
        inputs_hash=identity.inputs_hash,
    )


def _initial_cash(trades: list[Trade], period_start: date) -> int | None:
    """期初现金：区间前最后一条余额；缺失则首行反推；都不可得 → None（blocked）。"""
    balances_before = [
        trade for trade in trades if trade.trade_date < period_start and _is_cash_balance(trade)
    ]
    if balances_before:
        return int(balances_before[-1].cash_balance_units or 0)
    if trades and _is_cash_balance(trades[0]):
        # back_solved：首行余额 - 首行发生金额 = 该行之前的现金。
        return int((trades[0].cash_balance_units or 0) - trades[0].amount_units)
    return None


def _ending_cash(
    trades: list[Trade], period_start: date, period_end: date, *, initial_cash: int
) -> tuple[int, str]:
    """期末现金：区间内最后一条余额；没有则按现金流推导（调用方据此降级）。"""
    balances_in_period = [
        trade
        for trade in trades
        if period_start <= trade.trade_date <= period_end and _is_cash_balance(trade)
    ]
    if balances_in_period:
        return int(balances_in_period[-1].cash_balance_units or 0), "statement"
    period_net = sum(
        trade.amount_units for trade in trades if period_start <= trade.trade_date <= period_end
    )
    return initial_cash + period_net, "derived"


async def bridge_for_period(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    period_start: date,
    period_end: date,
    clock: Clock,
) -> BridgeResult:
    """计算区间桥接恒等式（API-027）。

    读取截至 `period_end` 的全部未删除明细（FIFO 路径依赖要求全历史），
    再分别取期初/期末锚点与区间分类现金流。
    """
    period = f"{period_start.year:04d}-{period_start.month:02d}"
    trades = await TradeRepository(session).balances_ordered(user_id, period_end=period_end)
    if not any(_is_cash_balance(trade) for trade in trades):
        return _blocked(period, clock)

    initial_cash = _initial_cash(trades, period_start)
    if initial_cash is None:
        return _blocked(period, clock)

    # 区间前持仓成本（FIFO open lots）与区间内平仓损益、期末未平仓成本。
    result_start = build_rounds(
        trades_to_events([t for t in trades if t.trade_date < period_start])
    )
    result_end = build_rounds(trades_to_events(trades))
    initial_open_cost = sum(lot.cost_units for lot in result_start.open_lots)
    open_cost = sum(lot.cost_units for lot in result_end.open_lots)
    realized_pnl = sum(
        item.pnl_units - item.dividend_units
        for item in result_end.closed_rounds
        if item.close_date is not None and period_start <= item.close_date <= period_end
    )

    ending_cash, cash_source = _ending_cash(
        trades, period_start, period_end, initial_cash=initial_cash
    )
    degraded = cash_source != "statement"

    cashflows = dividends = interest = fees = other = 0
    for trade in trades:
        if not (period_start <= trade.trade_date <= period_end):
            continue
        category = _category(trade)
        if category in _ROUND_CATEGORIES or category in _NON_CASH_CATEGORIES:
            continue
        if category == "dividend":
            dividends += trade.amount_units
        elif category == "interest":
            interest += trade.amount_units
        elif category == "fee":
            # fee 行 amount 为负（资金流出），桥接公式里 fees 是正数费用。
            fees += -trade.amount_units
        elif category == "transfer":
            cashflows += trade.amount_units
        else:
            other += trade.amount_units
            degraded = True

    identity = compute_bridge(
        BridgeLedger(
            initial_cash_units=initial_cash,
            initial_open_cost_units=initial_open_cost,
            ending_cash_units=ending_cash,
            cashflows_units=cashflows,
            trading_pnl_units=realized_pnl,
            dividends_units=dividends,
            interest_units=interest,
            fees_units=fees,
            other_units=other,
            ending_cash_source=cash_source,
            open_cost_units=open_cost,
        ),
        tolerance_units=TOLERANCE_UNITS,
    )
    return _to_result(period, identity, clock=clock, status="degraded" if degraded else "ok")


__all__ = ["TOLERANCE_UNITS", "BridgeResult", "bridge_for_period"]
