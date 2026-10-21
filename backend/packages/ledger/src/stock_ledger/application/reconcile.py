"""对账应用层：核对资金余额链，断链定位到具体行。

思路：按业务发生顺序把明细排成链，用 `上一行余额 + 本行带符号发生金额 == 本行余额`
逐步核对；容差用显式标度整数 `epsilon_units`。任何偏差都记成一条含行号/交易 ID/期望值/
实际值的 break——否则“资金对不上”只是一句无法行动的结论。
金额口径统一在解析器已归一：`Trade.amount_units` = 带符号的净现金流（买入为负、卖出为正、
已含费用），因此这里**不再按方向反号、也不再扣费用**（否则费用被扣两次、方向被翻两次，
真实账单会整片假断链）。
缺资金余额列时不做核对，返回 degraded（200 语义，IMPORT_MISSING_CASH_BALANCE）。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession
from stock_contracts.clock import Clock
from stock_contracts.constants import EPS_AMT
from stock_contracts.enums import ReconciliationStatus, Side
from stock_contracts.errors import ErrorCode
from stock_core.values import Money

from stock_ledger.models import Trade
from stock_ledger.repositories.reconciliation import ReconciliationRepository
from stock_ledger.repositories.trades import TradeRepository


@dataclass(slots=True)
class ReconciliationDTO:
    """对账结果返回体。"""

    id: str
    period_start: date
    period_end: date
    status: str
    break_count: int
    checked_rows: int
    epsilon_units: int
    breaks: list[dict[str, Any]]
    data_as_of: datetime
    generated_at: datetime
    warning_code: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "period_start": self.period_start.isoformat(),
            "period_end": self.period_end.isoformat(),
            "status": self.status,
            "break_count": self.break_count,
            "checked_rows": self.checked_rows,
            "epsilon_units": self.epsilon_units,
            "breaks": self.breaks,
            "data_as_of": self.data_as_of.isoformat(),
            "generated_at": self.generated_at.isoformat(),
            "warning_code": self.warning_code,
        }


def _cash_delta_units(trade: Trade) -> int:
    """本行资金变动（分）＝带符号发生金额：买入为负（流出）、卖出为正（流入）。

    费用已含在 `amount_units` 里（解析器统一口径），这里不做任何反号/扣费。
    """
    return trade.amount_units


async def reconcile(
    session: AsyncSession,
    *,
    clock: Clock,
    user_id: uuid.UUID,
    period_start: date,
    period_end: date,
    epsilon: Decimal = EPS_AMT,
) -> ReconciliationDTO:
    """核对 [period_start, period_end] 区间的资金余额链并 upsert 结果。"""
    epsilon_units = Money.from_decimal(epsilon).units
    trades = await TradeRepository(session).balances_ordered(
        user_id, period_start=period_start, period_end=period_end
    )

    has_balance = any(trade.cash_balance_units is not None for trade in trades)
    breaks: list[dict[str, Any]] = []
    checked = 0
    if has_balance:
        for index in range(1, len(trades)):
            previous = trades[index - 1]
            current = trades[index]
            if previous.cash_balance_units is None or current.cash_balance_units is None:
                continue
            if current.side == Side.UNKNOWN.value:
                # 方向未知的行不做链式核对：金额虽带符号，但方向语义无法独立复核。
                # 其余额仍是下一行的期初锚点，跳过本行不会造成后续假断链。
                continue
            expected = previous.cash_balance_units + _cash_delta_units(current)
            actual = current.cash_balance_units
            checked += 1
            if abs(expected - actual) > epsilon_units:
                breaks.append(
                    {
                        "index": index,
                        "trade_id": current.id,
                        "line_no": current.line_no,
                        "trade_date": current.trade_date.isoformat(),
                        "expected_units": expected,
                        "actual_units": actual,
                        "delta_units": actual - expected,
                    }
                )

    if not has_balance:
        status = ReconciliationStatus.DEGRADED.value
        warning_code: str | None = ErrorCode.IMPORT_MISSING_CASH_BALANCE.value
    elif breaks:
        status = ReconciliationStatus.BLOCKED.value
        warning_code = None
    else:
        status = ReconciliationStatus.OK.value
        warning_code = None

    now = clock.now()
    row = await ReconciliationRepository(session).upsert(
        user_id,
        period_start=period_start,
        period_end=period_end,
        status=status,
        break_count=len(breaks),
        checked_rows=checked,
        epsilon_units=epsilon_units,
        breaks=breaks,
        data_as_of=now,
        generated_at=now,
    )
    return ReconciliationDTO(
        id=row.id,
        period_start=row.period_start,
        period_end=row.period_end,
        status=row.status,
        break_count=row.break_count,
        checked_rows=row.checked_rows,
        epsilon_units=row.epsilon_units,
        breaks=list(row.breaks),
        data_as_of=row.data_as_of,
        generated_at=row.generated_at,
        warning_code=warning_code,
    )


__all__ = ["ReconciliationDTO", "reconcile"]
