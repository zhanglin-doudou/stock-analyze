"""账单旗标持仓推演：含费均价口径（费用只计一次）。"""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from typing import Any

from stock_review.trade_flags.service import _position_states


def _trade(
    trade_id: str,
    *,
    trade_date: date,
    line_no: int,
    side: str,
    quantity_units: int = 0,
    amount_units: int = 0,
    commission_units: int = 0,
) -> Any:
    """`_position_states` 只读这几个字段的轻量替身。"""
    return SimpleNamespace(
        id=trade_id,
        trade_date=trade_date,
        line_no=line_no,
        summary="证券买入" if side == "buy" else "证券卖出",
        security_code="688126",
        side=side,
        quantity_units=quantity_units,
        price_units=0,
        amount_units=amount_units,
        commission_units=commission_units,
        stamp_tax_units=0,
        other_fee_units=0,
    )


def test_position_states_counts_fee_once() -> None:
    """`amount_units` 已含费用：均价只按净现金流计一次，不能再叠加佣金字段。

    1000 股 @10.00，佣金 5.00 → 净现金流 -10005.00，均价 10.005（而非 10.010）。
    """
    buy = _trade(
        "trd_buy_fee",
        trade_date=date(2026, 4, 24),
        line_no=1,
        side="buy",
        quantity_units=10_000_000,
        amount_units=-1_000_500,
        commission_units=500,
    )
    sell = _trade(
        "trd_sell_fee",
        trade_date=date(2026, 4, 30),
        line_no=2,
        side="sell",
        quantity_units=1_000_000,
        amount_units=100_990,
    )
    state = _position_states([buy, sell])[sell.id]
    assert state.avg_cost_before == 10_005_000
