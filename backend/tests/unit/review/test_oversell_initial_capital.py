"""超卖未配对卖款并入期初资产（bridge / assets / nav 共用口径）单测。"""

from __future__ import annotations

import uuid
from datetime import date

from stock_core.fifo import EventKind, FifoEvent, build_rounds
from stock_ledger.models import Trade
from stock_review.application.bridge import _initial_cash, oversell_initial_units

USER = uuid.uuid4()


def _trade(day: date, *, amount_units: int, balance_units: int) -> Trade:
    return Trade(
        id=f"trd_{uuid.uuid4().hex[:12]}",
        user_id=USER,
        batch_id=None,
        trade_date=day,
        security_code="600519",
        security_name="贵州茅台",
        side="sell",
        quantity_units=100 * 10_000,
        price_units=10_000_000,
        amount_units=amount_units,
        commission_units=0,
        stamp_tax_units=0,
        other_fee_units=0,
        cash_balance_units=balance_units,
        cash_balance_available=True,
        category="证券卖出",
        occurrence_idx=0,
        line_no=1,
        source="import",
        version=1,
        summary="证券卖出",
    )


def _oversell_result(day: date, amount_units: int):
    event = FifoEvent(
        trade_id="trd_sell",
        trade_date=day,
        kind=EventKind.SELL,
        security_code="600519",
        security_name="贵州茅台",
        quantity_units=100 * 10_000,
        price_units=10_000_000,
        amount_units=amount_units,
        line_no=1,
    )
    return build_rounds([event])


def test_oversell_proceeds_in_period_fold_into_initial_cash() -> None:
    day = date(2026, 1, 2)
    trade = _trade(day, amount_units=100_000, balance_units=100_000)
    result = _oversell_result(day, 100_000)

    proceeds = oversell_initial_units(
        result, period_start=date(2026, 1, 1), period_end=date(2026, 1, 31)
    )
    assert proceeds == 100_000
    # 未配对卖款不是收益：并入后「期初资产 = 期末现金」，桥接残差为 0。
    assert _initial_cash([trade], date(2026, 1, 1)) == (0, "back_solved")
    assert _initial_cash([trade], date(2026, 1, 1), extra_units=proceeds) == (
        100_000,
        "back_solved",
    )
    assert result.oversell_proceeds_units == 100_000


def test_oversell_before_period_is_not_double_counted() -> None:
    day = date(2025, 12, 20)
    result = _oversell_result(day, 100_000)

    # 区间前的超卖已体现在期初现金锚点中，不得再次并入。
    assert (
        oversell_initial_units(result, period_start=date(2026, 1, 1), period_end=date(2026, 1, 31))
        == 0
    )
