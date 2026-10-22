"""FIFO 回合引擎 `build_rounds` 语义单测（INTERFACE §2.2）。

覆盖：稳定序、加仓/分批减仓、股息除息日归属、送转、配股、超卖 data_error、
open 回合、排序、守恒不变量与各聚合口。
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from stock_core.fifo import EventKind, FifoEvent, build_rounds

QTY_PER_SHARE = 10_000


def shares(count: int) -> int:
    """股数 → Qty 标度整数（1e-4）。"""
    return count * QTY_PER_SHARE


def cents(yuan: str) -> int:
    """元字符串 → 分（禁止 float）。"""
    return int(Decimal(yuan) * 100)


def price_of(yuan: str) -> int:
    """元字符串 → Price 标度整数（1e-6）。"""
    return int(Decimal(yuan) * 1_000_000)


def buy(
    trade_id: str,
    day: date,
    count: int,
    price: str = "10.00",
    *,
    code: str = "600000",
    name: str = "浦发银行",
    line_no: int = 0,
) -> FifoEvent:
    amount = -count * cents(price)
    return FifoEvent(
        trade_id=trade_id,
        trade_date=day,
        kind=EventKind.BUY,
        security_code=code,
        security_name=name,
        quantity_units=shares(count),
        price_units=price_of(price),
        amount_units=amount,
        line_no=line_no,
    )


def sell(
    trade_id: str,
    day: date,
    count: int,
    price: str = "12.00",
    *,
    code: str = "600000",
    name: str = "浦发银行",
    line_no: int = 0,
) -> FifoEvent:
    return FifoEvent(
        trade_id=trade_id,
        trade_date=day,
        kind=EventKind.SELL,
        security_code=code,
        security_name=name,
        quantity_units=shares(count),
        price_units=price_of(price),
        amount_units=count * cents(price),
        line_no=line_no,
    )


def dividend(
    trade_id: str,
    day: date,
    gross_units: int,
    tax_units: int = 0,
    *,
    code: str = "600000",
    name: str = "浦发银行",
    line_no: int = 0,
) -> FifoEvent:
    return FifoEvent(
        trade_id=trade_id,
        trade_date=day,
        kind=EventKind.DIVIDEND,
        security_code=code,
        security_name=name,
        amount_units=gross_units,
        dividend_tax_units=tax_units,
        line_no=line_no,
    )


def bonus(
    trade_id: str,
    day: date,
    *,
    ratio: str = "0.3",
    quantity_units: int = 0,
    code: str = "600000",
    line_no: int = 0,
) -> FifoEvent:
    return FifoEvent(
        trade_id=trade_id,
        trade_date=day,
        kind=EventKind.BONUS,
        security_code=code,
        security_name="浦发银行",
        quantity_units=quantity_units,
        ratio_units=int(Decimal(ratio) * 100_000_000),
        line_no=line_no,
    )


def rights(
    trade_id: str,
    day: date,
    count: int,
    payment_cents: int,
    *,
    code: str = "600000",
    line_no: int = 0,
) -> FifoEvent:
    return FifoEvent(
        trade_id=trade_id,
        trade_date=day,
        kind=EventKind.RIGHTS,
        security_code=code,
        security_name="浦发银行",
        quantity_units=shares(count),
        amount_units=-payment_cents,
        line_no=line_no,
    )


def other_kind(
    trade_id: str, day: date, kind: EventKind, amount_units: int = 0, code: str = "600000"
) -> FifoEvent:
    return FifoEvent(
        trade_id=trade_id,
        trade_date=day,
        kind=kind,
        security_code=code,
        amount_units=amount_units,
    )


# ── 空输入与基础配对 ──────────────────────────────────────


def test_empty_input_returns_empty_result() -> None:
    result = build_rounds([])
    assert result.rounds == ()
    assert result.fills == ()
    assert result.open_lots == ()
    assert result.data_errors == ()
    assert result.realized_pnl_units == 0
    assert result.trading_pnl_units == 0
    assert result.dividend_units == 0


def test_single_buy_full_sell_produces_closed_round() -> None:
    result = build_rounds(
        [
            buy("b1", date(2024, 1, 2), 100, "10.00", line_no=1),
            sell("s1", date(2024, 1, 10), 100, "12.00", line_no=2),
        ]
    )
    assert len(result.rounds) == 1
    round_ = result.rounds[0]
    assert round_.key == "b1:s1"
    assert round_.status == "closed"
    assert round_.security_code == "600000"
    assert round_.open_trade_id == "b1"
    assert round_.close_trade_id == "s1"
    assert round_.open_date == date(2024, 1, 2)
    assert round_.close_date == date(2024, 1, 10)
    assert round_.quantity_units == shares(100)
    assert round_.cost_units == cents("1000.00")
    assert round_.proceeds_units == cents("1200.00")
    assert round_.dividend_units == 0
    assert round_.pnl_units == cents("200.00")
    assert round_.return_rate_units == 20_000_000  # 0.2
    assert round_.holding_days == 6  # (01-02, 01-10] 工作日
    assert round_.data_error is False
    assert round_.oversell_units == 0
    assert result.open_lots == ()
    assert result.data_errors == ()

    assert len(round_.fills) == 1
    fill = round_.fills[0]
    assert fill.buy_trade_id == "b1"
    assert fill.sell_trade_id == "s1"
    assert fill.sequence == 1
    assert fill.quantity_units == shares(100)
    assert fill.buy_cost_units == cents("1000.00")
    assert fill.sell_proceeds_units == cents("1200.00")
    assert fill.pnl_units == cents("200.00")
    assert fill.holding_days == 6
    assert result.fills == (fill,)


def test_add_position_two_lots_fifo_pairing() -> None:
    result = build_rounds(
        [
            buy("b1", date(2024, 1, 2), 100, "10.00", line_no=1),
            buy("b2", date(2024, 1, 3), 100, "11.00", line_no=2),
            sell("s1", date(2024, 1, 10), 150, "12.00", line_no=3),
        ]
    )
    # 卖 150 没卖光 200 股 → 还是 open round
    assert len(result.closed_rounds) == 0
    assert len(result.rounds) == 1
    round_ = result.rounds[0]
    assert round_.key == "b1:open"
    assert round_.status == "open"
    assert round_.open_trade_id == "b1"
    assert round_.quantity_units == shares(150)
    assert round_.cost_units == cents("1550.00")  # 已消耗 1000+550，剩余 550 在 open_lots
    assert round_.proceeds_units == cents("1800.00")
    assert round_.dividend_units == 0
    assert round_.pnl_units == cents("250.00")
    # FIFO 配对填充
    assert [fill.buy_trade_id for fill in round_.fills] == ["b1", "b2"]
    assert [fill.sequence for fill in round_.fills] == [1, 2]
    assert [fill.quantity_units for fill in round_.fills] == [shares(100), shares(50)]
    assert [fill.buy_cost_units for fill in round_.fills] == [cents("1000.00"), cents("550.00")]
    assert [fill.sell_proceeds_units for fill in round_.fills] == [
        cents("1200.00"),
        cents("600.00"),
    ]
    # b2 剩余 50 股 → open_lots
    assert len(result.open_lots) == 1
    lot = result.open_lots[0]
    assert lot.buy_trade_id == "b2"
    assert lot.quantity_units == shares(50)
    assert lot.cost_units == cents("550.00")


def test_partial_reduce_three_sells_creates_three_rounds() -> None:
    events = [
        buy("b1", date(2024, 1, 2), 300, "10.00", line_no=1),
        sell("s1", date(2024, 1, 5), 100, "11.00", line_no=2),
        sell("s2", date(2024, 1, 8), 100, "12.00", line_no=3),
        sell("s3", date(2024, 1, 10), 100, "13.00", line_no=4),
    ]
    result = build_rounds(events)
    # 三次卖出后清仓 → 1 个 closed round
    assert len(result.rounds) == 1
    assert result.rounds[0].key == "b1:s3"
    assert result.rounds[0].status == "closed"
    assert result.rounds[0].quantity_units == shares(300)
    assert result.rounds[0].cost_units == cents("3000.00")
    assert result.rounds[0].proceeds_units == cents("3600.00")
    assert result.rounds[0].pnl_units == cents("600.00")
    assert result.rounds[0].close_date == date(2024, 1, 10)
    # 每次卖出都追加 fill
    assert [fill.sequence for fill in result.rounds[0].fills] == [1, 2, 3]
    assert [fill.sell_trade_id for fill in result.rounds[0].fills] == ["s1", "s2", "s3"]
    assert [fill.quantity_units for fill in result.rounds[0].fills] == [shares(100)] * 3
    assert [fill.pnl_units for fill in result.rounds[0].fills] == [
        cents("100.00"),
        cents("200.00"),
        cents("300.00"),
    ]
    assert result.open_lots == ()
    assert result.realized_pnl_units == cents("600.00")


def test_same_day_multi_fill_uses_line_no_tiebreak() -> None:
    events = [
        buy("b2", date(2024, 1, 2), 100, "11.00", line_no=5),
        buy("b1", date(2024, 1, 2), 100, "10.00", line_no=3),
        sell("s1", date(2024, 1, 10), 150, "12.00", line_no=9),
    ]
    result = build_rounds(events)
    round_ = result.rounds[0]
    assert [fill.buy_trade_id for fill in round_.fills] == ["b1", "b2"]
    assert round_.open_trade_id == "b1"
    assert result.open_lots[0].buy_trade_id == "b2"


def test_input_order_does_not_change_result() -> None:
    events = [
        buy("b1", date(2024, 1, 2), 100, "10.00", line_no=1),
        sell("s1", date(2024, 1, 10), 120, "12.00", line_no=2),
        buy("b2", date(2024, 1, 3), 100, "11.00", line_no=3),
        dividend("d1", date(2024, 1, 5), 500),
    ]
    forward = build_rounds(events)
    backward = build_rounds(reversed(events))
    assert forward == backward


def test_securities_are_independent_and_rounds_sorted() -> None:
    events = [
        buy("b-b", date(2024, 1, 2), 100, "10.00", code="000001", line_no=1),
        sell("s-b", date(2024, 1, 10), 100, "12.00", code="000001", line_no=2),
        buy("b-a", date(2024, 1, 3), 100, "10.00", code="600000", line_no=3),
        sell("s-a", date(2024, 1, 5), 100, "11.00", code="600000", line_no=4),
    ]
    result = build_rounds(events)
    assert [round_.security_code for round_ in result.rounds] == ["600000", "000001"]
    assert [round_.close_date for round_ in result.rounds] == [
        date(2024, 1, 5),
        date(2024, 1, 10),
    ]
    assert [fill.buy_trade_id for fill in result.fills] == ["b-a", "b-b"]


def test_other_event_kinds_are_ignored_by_engine() -> None:
    events = [
        buy("b1", date(2024, 1, 2), 100, "10.00", line_no=1),
        other_kind("i1", date(2024, 1, 3), EventKind.INTEREST, 50),
        other_kind("f1", date(2024, 1, 3), EventKind.FEE, -20),
        other_kind("t1", date(2024, 1, 4), EventKind.TRANSFER, 1_000),
        other_kind("n1", date(2024, 1, 4), EventKind.NEW_STOCK, 0),
        other_kind("o1", date(2024, 1, 5), EventKind.OTHER, 0),
        sell("s1", date(2024, 1, 10), 100, "12.00", line_no=2),
    ]
    result = build_rounds(events)
    assert len(result.rounds) == 1
    assert result.data_errors == ()
    assert result.open_lots == ()


# ── 超卖 data_error ──────────────────────────────────────


def test_partial_oversell_marks_data_error_without_negative_position() -> None:
    result = build_rounds(
        [
            buy("b1", date(2024, 1, 2), 100, "10.00", line_no=1),
            sell("s1", date(2024, 1, 10), 150, "13.00", line_no=2),
        ]
    )
    assert len(result.rounds) == 1
    round_ = result.rounds[0]
    assert round_.status == "data_error"
    assert round_.data_error is True
    assert round_.quantity_units == shares(100)
    # 可配对部分 = 100/150 股（成本 1000.00）；现金按可配对数量比例 = 1950.00 × 100/150 = 1300.00
    assert round_.cost_units == cents("1000.00")
    assert round_.proceeds_units == cents("1300.00")
    assert round_.pnl_units == cents("300.00")
    assert round_.oversell_units == shares(50)
    assert result.open_lots == ()
    assert len(result.data_errors) == 1
    error = result.data_errors[0]
    assert error.kind == "oversell"
    assert error.trade_id == "s1"
    assert error.quantity_units == shares(50)
    # data_error 回合不计入 closed / realized
    assert result.closed_rounds == ()
    assert result.realized_pnl_units == 0


def test_partial_oversell_proceeds_prorated_with_floor() -> None:
    # 事件金额 19.51 元 / 150 股，可配对 100 股 → 19.51 × 100/150 = 13.0066… → floor 到 1300 分
    event = FifoEvent(
        trade_id="s1",
        trade_date=date(2024, 1, 10),
        kind=EventKind.SELL,
        security_code="600000",
        quantity_units=shares(150),
        amount_units=1_951,
    )
    result = build_rounds([buy("b1", date(2024, 1, 2), 100, "10.00"), event])
    round_ = result.rounds[0]
    assert round_.proceeds_units == 1_300
    assert round_.pnl_units == 1_300 - cents("1000.00")
    assert sum(fill.sell_proceeds_units for fill in round_.fills) == round_.proceeds_units


def test_sell_without_any_holding_records_only_data_error() -> None:
    result = build_rounds([sell("s1", date(2024, 1, 10), 100, "12.00")])
    assert result.rounds == ()
    assert result.open_lots == ()
    assert len(result.data_errors) == 1
    assert result.data_errors[0].kind == "oversell"
    assert result.data_errors[0].quantity_units == shares(100)
    # 后续买入可正常配对，不被此前的无主卖出影响
    result = build_rounds(
        [
            sell("s1", date(2024, 1, 5), 100, "12.00"),
            buy("b1", date(2024, 1, 10), 100, "10.00"),
            sell("s2", date(2024, 1, 15), 100, "12.00"),
        ]
    )
    assert len(result.rounds) == 1
    assert result.rounds[0].key == "b1:s2"


# ── 非法事件 ─────────────────────────────────────────────


@pytest.mark.parametrize(
    ("event", "detail"),
    [
        (
            FifoEvent(
                trade_id="b0",
                trade_date=date(2024, 1, 2),
                kind=EventKind.BUY,
                quantity_units=0,
                amount_units=-100,
            ),
            "买入数量必须为正",
        ),
        (
            FifoEvent(
                trade_id="b-1",
                trade_date=date(2024, 1, 2),
                kind=EventKind.BUY,
                quantity_units=-shares(100),
                amount_units=-1_000,
            ),
            "买入数量必须为正",
        ),
        (
            FifoEvent(
                trade_id="s0",
                trade_date=date(2024, 1, 2),
                kind=EventKind.SELL,
                quantity_units=0,
            ),
            "卖出数量必须为正",
        ),
        (
            FifoEvent(
                trade_id="s-1",
                trade_date=date(2024, 1, 2),
                kind=EventKind.SELL,
                quantity_units=-shares(100),
            ),
            "卖出数量必须为正",
        ),
    ],
)
def test_invalid_buy_or_sell_quantity_records_data_error(event: FifoEvent, detail: str) -> None:
    result = build_rounds([event])
    assert result.rounds == ()
    assert len(result.data_errors) == 1
    assert result.data_errors[0].kind == "invalid_event"
    assert result.data_errors[0].detail == detail


def test_invalid_buy_does_not_create_lot_and_sell_afterwards_oversells() -> None:
    events = [
        FifoEvent(
            trade_id="bad",
            trade_date=date(2024, 1, 2),
            kind=EventKind.BUY,
            quantity_units=0,
        ),
        sell("s1", date(2024, 1, 10), 100, "12.00"),
    ]
    result = build_rounds(events)
    assert result.rounds == ()
    assert [error.kind for error in result.data_errors] == ["invalid_event", "oversell"]


# ── 股息 ─────────────────────────────────────────────────


def test_dividend_allocated_by_holding_at_ex_date_and_carried_into_round() -> None:
    events = [
        buy("b1", date(2024, 1, 2), 100, "10.00", line_no=1),
        buy("b2", date(2024, 1, 3), 300, "10.00", line_no=2),
        dividend("d1", date(2024, 1, 5), 400),
        sell("s1", date(2024, 1, 10), 200, "12.00", line_no=3),
        sell("s2", date(2024, 1, 11), 200, "12.00", line_no=4),
    ]
    result = build_rounds(events)
    # 两次卖出后清仓 → 1 个 closed round
    assert len(result.rounds) == 1
    round_ = result.rounds[0]
    assert round_.key == "b1:s2"
    assert round_.status == "closed"
    assert round_.quantity_units == shares(400)
    assert round_.cost_units == cents("4000.00")
    assert round_.proceeds_units == cents("4800.00")
    assert round_.dividend_units == 400
    assert round_.pnl_units == 80_400  # 80000 + 400

    # 3 个 fills：b1 全吃掉(100)+b2 的 100 → s1；b2 剩余 200 → s2
    assert [fill.sequence for fill in round_.fills] == [1, 2, 3]
    assert [fill.buy_trade_id for fill in round_.fills] == ["b1", "b2", "b2"]
    assert [fill.sell_trade_id for fill in round_.fills] == ["s1", "s1", "s2"]
    assert [fill.quantity_units for fill in round_.fills] == [
        shares(100),
        shares(100),
        shares(200),
    ]
    assert [fill.buy_cost_units for fill in round_.fills] == [
        cents("1000.00"),
        cents("1000.00"),
        cents("2000.00"),
    ]
    assert [fill.dividend_units for fill in round_.fills] == [100, 100, 200]
    assert [fill.pnl_units for fill in round_.fills] == [20_100, 20_100, 40_200]

    assert result.open_lots == ()
    assert result.dividend_units == 400
    assert result.trading_pnl_units == cents("800.00")  # 不含股息
    assert result.realized_pnl_units == 80_400  # closed round 的 pnl


def test_dividend_net_is_amount_minus_tax() -> None:
    events = [
        buy("b1", date(2024, 1, 2), 100, "10.00"),
        dividend("d1", date(2024, 1, 5), 500, 100),
        sell("s1", date(2024, 1, 10), 100, "10.00"),
    ]
    result = build_rounds(events)
    assert result.rounds[0].dividend_units == 400
    assert result.rounds[0].pnl_units == 400


def test_dividend_does_not_change_cost_and_open_lot_keeps_undistributed_share() -> None:
    events = [
        buy("b1", date(2024, 1, 2), 100, "10.00", line_no=1),
        buy("b2", date(2024, 1, 4), 200, "10.00", line_no=2),
        dividend("d1", date(2024, 1, 5), 101, 0, line_no=3),
        sell("s1", date(2024, 1, 10), 100, "10.00", line_no=4),
    ]
    result = build_rounds(events)
    # 卖 100 没清仓（剩 200）→ open round
    assert len(result.rounds) == 1
    round_ = result.rounds[0]
    assert round_.key == "b1:open"
    assert round_.status == "open"
    assert round_.quantity_units == shares(100)  # 已匹配
    assert round_.cost_units == cents("1000.00")  # 已消耗 b1 的 1000 元，剩余 2000 在 open_lots
    assert round_.proceeds_units == cents("1000.00")
    assert round_.dividend_units == 33  # 已消耗 lot 分到的股息（尾差 68 在 open_lots）
    assert round_.pnl_units == 33  # 100000 - 100000 + 33

    assert len(round_.fills) == 1
    fill = round_.fills[0]
    assert fill.sequence == 1
    assert fill.buy_trade_id == "b1"
    assert fill.sell_trade_id == "s1"
    assert fill.quantity_units == shares(100)
    assert fill.buy_cost_units == cents("1000.00")
    assert fill.sell_proceeds_units == cents("1000.00")
    assert fill.dividend_units == 33  # 101 × 100/300 = 33
    assert fill.pnl_units == 33

    # b2 剩余 200 股带未分配股息 68
    assert len(result.open_lots) == 1
    lot = result.open_lots[0]
    assert lot.buy_trade_id == "b2"
    assert lot.quantity_units == shares(200)
    assert lot.cost_units == cents("2000.00")
    assert lot.dividend_units == 68  # 尾差 101 - 33 = 68
    # result.dividend_units 聚合口 = Σ round.dividend（已消耗），不含 open_lots
    assert result.dividend_units == 33  # 只有 b1 被 SELL 消耗，b2 的 68 还在 open_lots


def test_dividend_without_holding_is_silently_ignored() -> None:
    result = build_rounds([dividend("d1", date(2024, 1, 2), 500)])
    assert result.rounds == ()
    assert result.data_errors == ()


def test_dividend_zero_net_allocates_zero() -> None:
    events = [
        buy("b1", date(2024, 1, 2), 100, "10.00"),
        dividend("d1", date(2024, 1, 5), 0),
        sell("s1", date(2024, 1, 10), 100, "10.00"),
    ]
    result = build_rounds(events)
    assert result.rounds[0].dividend_units == 0


def test_dividend_split_remainder_goes_to_last_lot() -> None:
    events = [
        buy("b1", date(2024, 1, 2), 1, "10.00", line_no=1),
        buy("b2", date(2024, 1, 2), 2, "10.00", line_no=2),
        dividend("d1", date(2024, 1, 3), 100),
    ]
    result = build_rounds(events)
    # 100 分按 1:2 → floor(33.33)=33，尾差 67 归最后一个 lot
    lots = {lot.buy_trade_id: lot for lot in result.open_lots}
    assert lots["b1"].dividend_units == 33
    assert lots["b2"].dividend_units == 67
    assert sum(lot.dividend_units for lot in result.open_lots) == 100


# ── 送转 ─────────────────────────────────────────────────


def test_bonus_by_ratio_scales_quantity_and_keeps_cost() -> None:
    events = [
        buy("b1", date(2024, 1, 2), 100, "10.00", line_no=1),
        bonus("e1", date(2024, 1, 3), ratio="0.3", line_no=2),
        sell("s1", date(2024, 1, 10), 130, "8.00", line_no=3),
    ]
    result = build_rounds(events)
    round_ = result.rounds[0]
    assert round_.quantity_units == shares(130)
    assert round_.cost_units == cents("1000.00")  # 总成本不变
    assert round_.proceeds_units == cents("1040.00")
    assert round_.pnl_units == cents("40.00")


def test_bonus_by_quantity_when_ratio_missing() -> None:
    events = [
        buy("b1", date(2024, 1, 2), 100, "10.00", line_no=1),
        bonus("e1", date(2024, 1, 3), ratio="0", quantity_units=shares(30), line_no=2),
    ]
    result = build_rounds(events)
    assert result.open_lots[0].quantity_units == shares(130)
    assert result.open_lots[0].cost_units == cents("1000.00")


def test_bonus_distribution_across_lots_and_half_up_rounding() -> None:
    events = [
        buy("b1", date(2024, 1, 2), 1, "10.00", line_no=1),
        buy("b2", date(2024, 1, 2), 2, "10.00", line_no=2),
        bonus("e1", date(2024, 1, 3), ratio="0.5", line_no=3),
    ]
    result = build_rounds(events)
    # 3 股 × 0.5 = 1.5 股 = 15000 Qty units；按 1:2 → 5000 / 10000
    lots = {lot.buy_trade_id: lot for lot in result.open_lots}
    assert lots["b1"].quantity_units == shares(1) + 5_000
    assert lots["b2"].quantity_units == shares(2) + 10_000
    assert lots["b1"].cost_units == cents("10.00")
    assert lots["b2"].cost_units == cents("20.00")


def test_bonus_rounds_half_up_at_qty_unit_precision() -> None:
    # 1 股 × 0.00005 = 0.00005 股 = 0.5 Qty unit → HALF_UP → 1 unit
    events = [
        buy("b1", date(2024, 1, 2), 1, "10.00"),
        bonus("e1", date(2024, 1, 3), ratio="0.00005"),
    ]
    result = build_rounds(events)
    assert result.open_lots[0].quantity_units == shares(1) + 1


def test_bonus_without_holding_is_invalid_event() -> None:
    result = build_rounds([bonus("e1", date(2024, 1, 2), ratio="0", quantity_units=shares(30))])
    assert result.rounds == ()
    assert len(result.data_errors) == 1
    assert result.data_errors[0].kind == "invalid_event"
    assert result.data_errors[0].detail == "送转事件无可用持仓"


def test_bonus_without_holding_and_zero_added_is_ignored() -> None:
    result = build_rounds([bonus("e1", date(2024, 1, 2), ratio="0", quantity_units=0)])
    assert result.rounds == ()
    assert result.data_errors == ()


def test_bonus_with_zero_added_is_noop() -> None:
    events = [
        buy("b1", date(2024, 1, 2), 100, "10.00"),
        bonus("e1", date(2024, 1, 3), ratio="0", quantity_units=0),
    ]
    result = build_rounds(events)
    assert result.open_lots[0].quantity_units == shares(100)


# ── 配股 ─────────────────────────────────────────────────


def test_rights_payment_increases_cost_and_does_not_create_round() -> None:
    events = [
        buy("b1", date(2024, 1, 2), 100, "10.00", line_no=1),
        rights("r1", date(2024, 1, 3), 20, cents("200.00"), line_no=2),
        sell("s1", date(2024, 1, 10), 120, "11.00", line_no=3),
    ]
    result = build_rounds(events)
    # rights 不新增回合：只有 1 个 closed 回合
    assert len(result.rounds) == 1
    round_ = result.rounds[0]
    assert round_.quantity_units == shares(120)
    assert round_.cost_units == cents("1200.00")  # 1000.00 买入 + 200.00 缴款
    assert round_.pnl_units == cents("120.00")  # 1320.00 - 1200.00
    assert result.data_errors == ()


def test_rights_distribution_across_lots_with_remainder_to_last_lot() -> None:
    events = [
        FifoEvent(
            trade_id="b1",
            trade_date=date(2024, 1, 2),
            kind=EventKind.BUY,
            security_code="600000",
            quantity_units=shares(1),
            amount_units=-cents("10.00"),
            line_no=1,
        ),
        FifoEvent(
            trade_id="b2",
            trade_date=date(2024, 1, 2),
            kind=EventKind.BUY,
            security_code="600000",
            quantity_units=shares(2),
            amount_units=-cents("20.00"),
            line_no=2,
        ),
        rights("r1", date(2024, 1, 3), 1, 100, line_no=3),
    ]
    result = build_rounds(events)
    lots = {lot.buy_trade_id: lot for lot in result.open_lots}
    # 新增 1 股按 1:2 → 3333 / 6667 Qty units；缴款 100 分 → 33 / 67
    assert lots["b1"].quantity_units == shares(1) + 3_333
    assert lots["b2"].quantity_units == shares(2) + 6_667
    assert lots["b1"].cost_units == cents("10.00") + 33
    assert lots["b2"].cost_units == cents("20.00") + 67
    assert sum(lot.quantity_units for lot in result.open_lots) == shares(4)
    assert sum(lot.cost_units for lot in result.open_lots) == cents("30.00") + 100


def test_rights_with_invalid_quantity_is_invalid_event() -> None:
    result = build_rounds(
        [
            buy("b1", date(2024, 1, 2), 100, "10.00"),
            FifoEvent(
                trade_id="r0",
                trade_date=date(2024, 1, 3),
                kind=EventKind.RIGHTS,
                security_code="600000",
                quantity_units=0,
                amount_units=-100,
            ),
        ]
    )
    assert [error.kind for error in result.data_errors] == ["invalid_event"]
    assert result.data_errors[0].detail == "配股数量必须为正"


def test_rights_without_holding_is_unmatched_rights() -> None:
    result = build_rounds([rights("r1", date(2024, 1, 2), 20, cents("200.00"))])
    assert result.rounds == ()
    assert len(result.data_errors) == 1
    assert result.data_errors[0].kind == "unmatched_rights"
    assert result.data_errors[0].quantity_units == shares(20)


# ── open 回合与排序 ──────────────────────────────────────


def test_open_rounds_per_lot_sorted_by_open_date_then_key() -> None:
    events = [
        buy("b2", date(2024, 1, 5), 100, "11.00", line_no=1),
        buy("b1", date(2024, 1, 5), 100, "10.00", line_no=2),
        buy("b0", date(2024, 1, 2), 100, "9.00", line_no=3),
    ]
    result = build_rounds(events)
    # 新语义：一个证券一个周期 → 只有 1 个 open round
    assert len(result.rounds) == 1
    round_ = result.rounds[0]
    assert round_.key == "b0:open"
    assert round_.status == "open"
    assert round_.close_trade_id is None
    assert round_.close_date is None
    assert round_.fills == ()
    assert round_.holding_days == 0
    assert round_.proceeds_units == 0
    assert round_.quantity_units == 0  # 还没卖过
    assert round_.cost_units == 0  # 没有 SELL，已消耗 cost = 0
    assert round_.dividend_units == 0
    assert round_.pnl_units == 0
    assert round_.return_rate_units == 0

    # open_lots 按 open_date 排序，同日期按 line_no
    assert [lot.buy_trade_id for lot in result.open_lots] == ["b0", "b2", "b1"]
    assert [lot.quantity_units for lot in result.open_lots] == [shares(100)] * 3
    assert [lot.cost_units for lot in result.open_lots] == [
        cents("900.00"),
        cents("1100.00"),
        cents("1000.00"),
    ]


def test_closed_rounds_sort_before_open_rounds() -> None:
    events = [
        buy("b2", date(2024, 1, 20), 100, "10.00", line_no=1),
        buy("b1", date(2024, 1, 2), 100, "10.00", line_no=2),
        sell("s1", date(2024, 1, 10), 100, "12.00", line_no=3),
    ]
    result = build_rounds(events)
    assert [round_.key for round_ in result.rounds] == ["b1:s1", "b2:open"]


def test_fills_flatten_in_round_order() -> None:
    events = [
        sell("s2", date(2024, 1, 10), 100, "12.00", code="000001", line_no=1),
        buy("b2", date(2024, 1, 2), 100, "10.00", code="000001", line_no=2),
        sell("s1", date(2024, 1, 5), 100, "11.00", code="600000", line_no=3),
        buy("b1", date(2024, 1, 2), 100, "10.00", code="600000", line_no=4),
    ]
    result = build_rounds(events)
    assert [round_.key for round_ in result.rounds] == ["b1:s1", "b2:s2"]
    assert [fill.sell_trade_id for fill in result.fills] == ["s1", "s2"]


def test_security_name_prefers_sell_then_buy() -> None:
    events = [
        buy("b1", date(2024, 1, 2), 100, "10.00", name="旧名", line_no=1),
        sell("s1", date(2024, 1, 5), 50, "11.00", name="", line_no=2),
    ]
    result = build_rounds(events)
    # 卖 50 没清仓 → 只有 1 个 open round
    assert len(result.rounds) == 1
    assert result.rounds[0].security_name == "旧名"  # SELL 名为空 → 回落第一个 BUY 名

    result = build_rounds(
        [
            buy("b1", date(2024, 1, 2), 100, "10.00", name="旧名", line_no=1),
            sell("s1", date(2024, 1, 5), 50, "11.00", name="新名", line_no=2),
        ]
    )
    assert len(result.rounds) == 1
    assert result.rounds[0].security_name == "新名"  # SELL 名非空优先


# ── 持仓天数与收益率 ────────────────────────────────────


def test_holding_days_weighted_by_quantity() -> None:
    events = [
        buy("b1", date(2024, 1, 2), 100, "10.00", line_no=1),
        buy("b2", date(2024, 1, 8), 100, "10.00", line_no=2),
        sell("s1", date(2024, 1, 10), 200, "10.00", line_no=3),
    ]
    result = build_rounds(events)
    round_ = result.rounds[0]
    assert [fill.holding_days for fill in round_.fills] == [6, 2]
    # 数量加权平均：(6*100 + 2*100) / 200 = 4
    assert round_.holding_days == 4


def test_same_day_round_holding_days_is_one() -> None:
    events = [
        buy("b1", date(2024, 1, 2), 100, "10.00", line_no=1),
        sell("s1", date(2024, 1, 2), 100, "11.00", line_no=2),
    ]
    result = build_rounds(events)
    assert result.rounds[0].holding_days == 1
    assert result.rounds[0].fills[0].holding_days == 1


def test_open_round_return_rate_uses_dividend_over_cost() -> None:
    events = [
        buy("b1", date(2024, 1, 2), 100, "10.00"),
        dividend("d1", date(2024, 1, 5), 500),
    ]
    result = build_rounds(events)
    open_round = result.rounds[0]
    assert open_round.status == "open"
    assert open_round.quantity_units == 0  # 还没卖过
    assert open_round.fills == ()
    assert open_round.proceeds_units == 0
    # 没有 SELL → 已消耗 cost=0，股息还在 open_lots 里（未消耗）
    assert open_round.cost_units == 0
    assert open_round.dividend_units == 0
    # pnl = proceeds - cost + dividend = 0 - 0 + 0
    assert open_round.pnl_units == 0
    # return_rate = pnl / cost，但 cost=0 时为 0（约定）
    assert open_round.return_rate_units == 0


def test_zero_cost_round_has_zero_return_rate() -> None:
    events = [
        FifoEvent(
            trade_id="b1",
            trade_date=date(2024, 1, 2),
            kind=EventKind.BUY,
            security_code="600000",
            quantity_units=shares(100),
            amount_units=0,
        ),
        sell("s1", date(2024, 1, 10), 100, "12.00"),
    ]
    result = build_rounds(events)
    assert result.rounds[0].cost_units == 0
    assert result.rounds[0].pnl_units == cents("1200.00")
    assert result.rounds[0].return_rate_units == 0


# ── 守恒不变量 ───────────────────────────────────────────


def test_conservation_invariants_for_mixed_events() -> None:
    events = [
        buy("b1", date(2024, 1, 2), 100, "10.00", line_no=1),
        buy("b2", date(2024, 1, 3), 200, "11.00", line_no=2),
        dividend("d1", date(2024, 1, 4), 600, 100, line_no=3),
        bonus("e1", date(2024, 1, 5), ratio="0.3", line_no=4),
        rights("r1", date(2024, 1, 6), 30, cents("300.00"), line_no=5),
        sell("s1", date(2024, 1, 8), 250, "12.00", line_no=6),
        sell("s2", date(2024, 1, 9), 100, "12.00", line_no=7),
    ]
    result = build_rounds(events)

    # 卖 250+100=350，送转 300×0.3=90，配股 30 → 总 420 → 剩余 70 → open round
    assert len(result.rounds) == 1
    round_ = result.rounds[0]
    assert round_.key == "b1:open"
    assert round_.status == "open"
    assert round_.quantity_units == shares(350)
    assert round_.cost_units == 289_999  # 已消耗 lot cost（不是全周期 350000）
    assert round_.proceeds_units == cents("4200.00")  # 350×12
    assert round_.dividend_units == 416  # 已消耗 lot 分到的股息
    assert round_.pnl_units == 130_417  # 420000 - 289999 + 416

    # 3 fills 合计 350 股
    assert len(round_.fills) == 3
    assert sum(fill.quantity_units for fill in round_.fills) == round_.quantity_units
    # fills 的 pnl 合计
    assert sum(fill.pnl_units for fill in round_.fills) == 58_166 + 37_846 + 34_405  # 130417

    # open_lots: b2 剩余 70 股
    assert len(result.open_lots) == 1
    lot = result.open_lots[0]
    assert lot.buy_trade_id == "b2"
    assert lot.quantity_units == shares(70)
    assert lot.cost_units == 60_001
    assert lot.dividend_units == 84

    # === 守恒不变量 ===

    # pnl 恒等式：pnl = proceeds - cost + dividend（open / closed 都适用）
    for r in result.rounds:
        assert r.pnl_units == r.proceeds_units - r.cost_units + r.dividend_units

    # cost 守恒：已消耗 cost + 剩余 lot cost = 总投入
    assert round_.cost_units + sum(lot.cost_units for lot in result.open_lots) == cents(
        "1000.00"
    ) + cents("2200.00") + cents("300.00")  # 350000 分

    # 股息守恒：已消耗 dividend + 剩余 lot dividend = 净股息总额
    assert (
        round_.dividend_units + sum(lot.dividend_units for lot in result.open_lots) == 500
    )  # 600-100 tax

    # 数量守恒：matched + open = 总 420 股
    assert round_.quantity_units + sum(lot.quantity_units for lot in result.open_lots) == shares(
        420
    )


def test_build_rounds_is_deterministic() -> None:
    events = [
        buy("b1", date(2024, 1, 2), 100, "10.00", line_no=1),
        sell("s1", date(2024, 1, 10), 100, "12.00", line_no=2),
    ]
    assert build_rounds(events) == build_rounds(events)
    assert build_rounds(iter(events)) == build_rounds(events)
