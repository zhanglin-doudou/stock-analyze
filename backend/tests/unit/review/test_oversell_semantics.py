"""超卖口径单测：超卖部分视同资金划转（状态 oversell、不计收益、不降级）。"""

from __future__ import annotations

from datetime import date

from stock_core.fifo import EventKind, FifoEvent, build_rounds


def _buy(day: date, *, qty_units: int, cost_units: int) -> FifoEvent:
    return FifoEvent(
        trade_id="trd_buy",
        trade_date=day,
        kind=EventKind.BUY,
        security_code="600519",
        security_name="贵州茅台",
        quantity_units=qty_units,
        price_units=10_000_000,
        amount_units=-cost_units,
        line_no=1,
    )


def _sell(day: date, *, qty_units: int, amount_units: int) -> FifoEvent:
    return FifoEvent(
        trade_id="trd_sell",
        trade_date=day,
        kind=EventKind.SELL,
        security_code="600519",
        security_name="贵州茅台",
        quantity_units=qty_units,
        price_units=10_000_000,
        amount_units=amount_units,
        line_no=2,
    )


def test_full_oversell_records_round_without_fills() -> None:
    """无任何持仓的卖出：落库成 oversell 回合（无 fill），卖款留痕供人工修订与资产口径使用。"""
    result = build_rounds([_sell(date(2026, 1, 2), qty_units=100 * 10_000, amount_units=100_000)])

    assert len(result.rounds) == 1
    round_ = result.rounds[0]
    assert round_.key == "trd_sell:oversell"
    assert round_.status == "oversell"
    assert round_.data_error is False
    assert round_.quantity_units == 100 * 10_000
    assert round_.oversell_units == 100 * 10_000
    assert round_.proceeds_units == 100_000
    # 纯超卖不构成交易损益：成本/盈亏/收益率恒 0
    assert round_.cost_units == 0
    assert round_.pnl_units == 0
    assert round_.return_rate_units == 0
    assert round_.fills == ()
    assert result.oversell_proceeds_units == 100_000
    assert [(e.kind, e.proceeds_units) for e in result.data_errors] == [("oversell", 100_000)]
    # 不进 closed / realized
    assert result.closed_rounds == ()
    assert result.realized_pnl_units == 0


def test_partial_oversell_round_status_is_oversell() -> None:
    """部分超卖：已配对部分正常平仓，回合状态单独标 `oversell` 且不再标 data_error。"""
    result = build_rounds(
        [
            _buy(date(2026, 1, 2), qty_units=100 * 10_000, cost_units=100_000),
            _sell(date(2026, 1, 9), qty_units=150 * 10_000, amount_units=150_000),
        ]
    )

    assert len(result.rounds) == 1
    round_ = result.rounds[0]
    assert round_.status == "oversell"
    assert round_.oversell_units == 50 * 10_000
    assert round_.data_error is False
    # 已配对部分（100 股，买入成本 1000 元、卖出净额 1000 元）损益为 0；
    # 未配对卖款只留痕，不进 fill、不计收益。
    assert round_.pnl_units == 0
    assert result.oversell_proceeds_units == 50_000
