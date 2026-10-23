"""FIFO 值对象（types.py）单测：枚举、稳定序、不可变性、聚合口。"""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import date

import pytest
from stock_core.fifo import (
    DataError,
    EventKind,
    FifoEvent,
    FifoResult,
    Fill,
    OpenLot,
    Round,
)
from stock_core.fifo import engine as fifo_engine
from stock_core.fifo import types as fifo_types


def test_event_kind_enum_values() -> None:
    assert {kind.value for kind in EventKind} == {
        "buy",
        "sell",
        "dividend",
        "bonus",
        "rights",
        "interest",
        "fee",
        "transfer",
        "new_stock",
        "other",
    }
    assert EventKind("buy") is EventKind.BUY


def test_stable_key_matches_frozen_contract_order() -> None:
    event = FifoEvent(
        trade_id="t1",
        trade_date=date(2024, 1, 2),
        kind=EventKind.BUY,
        quantity_units=100,
        price_units=200,
        summary="证券买入",
        line_no=7,
    )
    assert event.stable_key == (date(2024, 1, 2), 7, "证券买入", 100, 200, "t1")


def test_stable_key_sorting_breaks_ties_by_line_no_then_fields() -> None:
    base = {
        "trade_date": date(2024, 1, 2),
        "kind": EventKind.BUY,
        "quantity_units": 100,
        "price_units": 200,
    }
    earlier = FifoEvent(trade_id="b", line_no=1, **base)  # type: ignore[arg-type]
    later = FifoEvent(trade_id="a", line_no=2, **base)  # type: ignore[arg-type]
    assert sorted([later, earlier], key=lambda event: event.stable_key) == [earlier, later]


def test_fifo_event_defaults() -> None:
    event = FifoEvent(trade_id="t", trade_date=date(2024, 1, 2), kind=EventKind.OTHER)
    assert event.security_code == ""
    assert event.security_name == ""
    assert event.quantity_units == 0
    assert event.price_units == 0
    assert event.amount_units == 0
    assert event.commission_units == 0
    assert event.stamp_tax_units == 0
    assert event.other_fee_units == 0
    assert event.dividend_tax_units == 0
    assert event.ratio_units == 0
    assert event.line_no == 0
    assert event.summary == ""


def test_fifo_event_is_frozen() -> None:
    event = FifoEvent(trade_id="t", trade_date=date(2024, 1, 2), kind=EventKind.BUY)
    with pytest.raises(FrozenInstanceError):
        event.trade_id = "other"  # type: ignore[misc]


def test_value_objects_are_frozen() -> None:
    fill = Fill(
        buy_trade_id="b",
        sell_trade_id="s",
        sequence=1,
        quantity_units=1,
        buy_cost_units=2,
        sell_proceeds_units=3,
        dividend_units=4,
        pnl_units=5,
        open_date=date(2024, 1, 2),
        close_date=date(2024, 1, 3),
        holding_days=1,
    )
    round_ = Round(
        key="b:s",
        security_code="600000",
        security_name="浦发银行",
        status="closed",
        open_trade_id="b",
        close_trade_id="s",
        open_date=date(2024, 1, 2),
        close_date=date(2024, 1, 3),
        quantity_units=1,
        cost_units=2,
        proceeds_units=3,
        dividend_units=4,
        pnl_units=5,
        return_rate_units=6,
        holding_days=1,
        data_error=False,
        oversell_units=0,
        fills=(fill,),
    )
    lot = OpenLot(
        security_code="600000",
        buy_trade_id="b",
        open_date=date(2024, 1, 2),
        quantity_units=1,
        cost_units=2,
        dividend_units=3,
    )
    error = DataError(
        trade_id="s",
        security_code="600000",
        trade_date=date(2024, 1, 3),
        kind="oversell",
        quantity_units=1,
        detail="超卖",
    )
    with pytest.raises(FrozenInstanceError):
        round_.pnl_units = 0  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        fill.pnl_units = 0  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        lot.cost_units = 0  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        error.detail = "x"  # type: ignore[misc]


def test_fifo_result_empty_aggregates() -> None:
    result = FifoResult(rounds=(), fills=(), open_lots=(), data_errors=())
    assert result.closed_rounds == ()
    assert result.open_rounds == ()
    assert result.realized_pnl_units == 0
    assert result.trading_pnl_units == 0
    assert result.dividend_units == 0


def test_fifo_result_aggregates_exclude_data_error_from_closed() -> None:
    def make_round(
        key: str, status: str, *, pnl: int, dividend: int, close_trade_id: str | None
    ) -> Round:
        return Round(
            key=key,
            security_code="600000",
            security_name="浦发银行",
            status=status,
            open_trade_id=key.split(":")[0],
            close_trade_id=close_trade_id,
            open_date=date(2024, 1, 2),
            close_date=date(2024, 1, 10) if close_trade_id else None,
            quantity_units=100,
            cost_units=1000,
            proceeds_units=pnl + 1000 - dividend,
            dividend_units=dividend,
            pnl_units=pnl,
            return_rate_units=0,
            holding_days=1 if close_trade_id else 0,
            data_error=status == "data_error",
            oversell_units=5 if status == "data_error" else 0,
            fills=(),
        )

    closed = make_round("b1:s1", "closed", pnl=500, dividend=100, close_trade_id="s1")
    errored = make_round("b2:s2", "data_error", pnl=200, dividend=50, close_trade_id="s2")
    open_ = make_round("b3:open", "open", pnl=0, dividend=30, close_trade_id=None)
    result = FifoResult(rounds=(closed, errored, open_), fills=(), open_lots=(), data_errors=())
    assert result.closed_rounds == (closed,)
    assert result.open_rounds == (open_,)
    assert result.realized_pnl_units == 500
    assert result.trading_pnl_units == 400
    # 股息聚合 = closed + open（data_error 不计入）
    assert result.dividend_units == 130


def test_fifo_package_reexports_frozen_api() -> None:
    assert fifo_types.FifoEvent is FifoEvent
    assert fifo_engine.build_rounds is not None
    import stock_core.fifo as fifo

    assert fifo.build_rounds is fifo_engine.build_rounds
    for name in fifo.__all__:
        assert hasattr(fifo, name)
