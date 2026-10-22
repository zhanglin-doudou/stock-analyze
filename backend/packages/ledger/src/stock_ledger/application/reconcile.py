"""对账应用层：核对资金余额链，断链定位到具体行并自动补差。

思路：按业务发生顺序把明细排成链，用 `上一行余额 + 本行带符号发生金额 == 本行余额`
逐步核对；容差用显式标度整数 `epsilon_units`。任何偏差都记成一条含行号/交易 ID/期望值/
实际值的 break——否则“资金对不上”只是一句无法行动的结论。

断链自愈：发现偏差时写入一条 `side=missing`、`source=system` 的补差流水（日期取断链行
日期，摘要「对账自动补差」，金额 = 实际余额 − 期望余额，余额 = 本行余额 − 本行发生金额），
使 `上一行 → 补差行 → 断链行` 两条链式断言都成立。补差一次后链即闭合，重复调用不再插入
（幂等）；响应仍返回本次发现的断链并附 `filled/filled_trade_id`，`filled_count` 为本轮补差数。
补差行靠「同日期同 line_no 时 missing 排在断链行之前」定位，因此只对带账单行号的导入行自动
补差；手工录入行（`line_no` 为空）无法稳定插到两行之间，保持 `blocked` 交人工修复。

金额口径统一在解析器已归一：`Trade.amount_units` = 带符号的净现金流（买入为负、卖出为正、
已含费用），因此这里**不再按方向反号、也不再扣费用**（否则费用被扣两次、方向被翻两次，
真实账单会整片假断链）。
缺资金余额列时不做核对，返回 degraded（200 语义，IMPORT_MISSING_CASH_BALANCE）。
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession
from stock_contracts.clock import Clock
from stock_contracts.constants import EPS_AMT
from stock_contracts.enums import ReconciliationStatus, Side, TradeSource
from stock_contracts.errors import ErrorCode
from stock_core.values import Money

from stock_ledger.models import Trade
from stock_ledger.repositories.reconciliation import ReconciliationRepository
from stock_ledger.repositories.trades import TradeRepository, TradeRow

# 补差流水的固定业务描述（日期/金额/余额按断链现场计算，其余字段固定便于识别与过滤）。
_FILL_SECURITY_CODE = "CASH"
_FILL_SECURITY_NAME = "对账补差"
_FILL_SUMMARY = "对账自动补差"
_FILL_CATEGORY = "other"


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
    filled_count: int = 0

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
            "filled_count": self.filled_count,
            "data_as_of": self.data_as_of.isoformat(),
            "generated_at": self.generated_at.isoformat(),
            "warning_code": self.warning_code,
        }


def _cash_delta_units(trade: Trade) -> int:
    """本行资金变动（分）＝带符号发生金额：买入为负（流出）、卖出为正（流入）。

    费用已含在 `amount_units` 里（解析器统一口径），这里不做任何反号/扣费。
    """
    return trade.amount_units


def _fill_dedupe_key(previous: Trade, current: Trade) -> str:
    """补差行去重键：按「断链前后两行」确定，同一断链重复补差会命中同一键。"""
    payload = f"recon|{previous.id}|{current.id}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


async def _insert_fill(
    repo: TradeRepository,
    user_id: uuid.UUID,
    previous: Trade,
    current: Trade,
    *,
    previous_balance: int,
    current_balance: int,
    fill_batch_id: str | None,
) -> tuple[Trade, bool]:
    """为一条断链插入 side=missing 的补差流水，返回 `(行, 是否本次新插入)`。

    余额取 `本行余额 − 本行发生金额`：既让 `上一行余额 + 补差金额 == 补差行余额`，
    也让 `补差行余额 + 本行发生金额 == 本行余额`，补差后链即闭合。
    """
    expected = previous_balance + _cash_delta_units(current)
    return await repo.insert_one_or_existing(
        user_id,
        TradeRow(
            trade_date=current.trade_date,
            security_code=_FILL_SECURITY_CODE,
            security_name=_FILL_SECURITY_NAME,
            summary=_FILL_SUMMARY,
            side=Side.MISSING.value,
            quantity_units=0,
            price_units=0,
            amount_units=current_balance - expected,
            cash_balance_units=current_balance - _cash_delta_units(current),
            cash_balance_available=True,
            category=_FILL_CATEGORY,
            dedupe_key=_fill_dedupe_key(previous, current),
            batch_id=fill_batch_id,
            source=TradeSource.SYSTEM.value,
            line_no=current.line_no,
        ),
    )


async def reconcile(
    session: AsyncSession,
    *,
    clock: Clock,
    user_id: uuid.UUID,
    period_start: date,
    period_end: date,
    epsilon: Decimal = EPS_AMT,
    fill_batch_id: str | None = None,
) -> ReconciliationDTO:
    """核对 [period_start, period_end] 区间的资金余额链、自动补差并 upsert 结果。

    `fill_batch_id` 不为空时，补差行归属到该导入批次：撤销批次会一并软删补差行。
    """
    epsilon_units = Money.from_decimal(epsilon).units
    repo = TradeRepository(session)
    trades = await repo.balances_ordered(user_id, period_start=period_start, period_end=period_end)

    has_balance = any(trade.cash_balance_units is not None for trade in trades)
    has_missing_rows = any(trade.side == Side.MISSING.value for trade in trades)
    breaks: list[dict[str, Any]] = []
    checked = 0
    filled_count = 0
    unfilled = 0
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
            delta = actual - expected
            if abs(delta) <= epsilon_units:
                continue
            if current.line_no is None:
                # 手工录入行没有账单行号，补差行无法稳定插到「上一行→本行」之间
                # （排序只按日期/行号/id）；这类断链保持 blocked，交人工修复。
                unfilled += 1
                breaks.append(
                    {
                        "index": index,
                        "trade_id": current.id,
                        "line_no": None,
                        "trade_date": current.trade_date.isoformat(),
                        "expected_units": expected,
                        "actual_units": actual,
                        "delta_units": delta,
                        "filled": False,
                        "filled_trade_id": None,
                    }
                )
                continue
            fill, created = await _insert_fill(
                repo,
                user_id,
                previous,
                current,
                previous_balance=previous.cash_balance_units,
                current_balance=actual,
                fill_batch_id=fill_batch_id,
            )
            if created:
                filled_count += 1
            has_missing_rows = True
            breaks.append(
                {
                    "index": index,
                    "trade_id": current.id,
                    "line_no": current.line_no,
                    "trade_date": current.trade_date.isoformat(),
                    "expected_units": expected,
                    "actual_units": actual,
                    "delta_units": delta,
                    "filled": True,
                    "filled_trade_id": fill.id,
                }
            )

    warning_code: str | None
    if not has_balance:
        status = ReconciliationStatus.DEGRADED.value
        warning_code = ErrorCode.IMPORT_MISSING_CASH_BALANCE.value
    elif unfilled:
        status = ReconciliationStatus.BLOCKED.value
        warning_code = None
    elif filled_count or has_missing_rows:
        # 本轮补过差、或区间内已有系统补差行：链已闭合但数据经过自愈，降级提示。
        status = ReconciliationStatus.DEGRADED.value
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
        filled_count=filled_count,
    )


__all__ = ["ReconciliationDTO", "reconcile"]
