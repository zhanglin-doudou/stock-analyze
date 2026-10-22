"""FIFO 引擎 hypothesis 属性测试：数量/成本/股息守恒、无负持仓、pnl 恒等式。

守恒期望由测试内**独立聚合重放**（只跟踪数量与现金流，不复用生产代码）给出，
与 `build_rounds` 的输出逐项对账。
"""

from __future__ import annotations

from datetime import date, timedelta

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from stock_core.fifo import EventKind, FifoEvent, build_rounds

SHARE = 10_000  # Qty 1e-4：1 股
RATIO_SCALE = 100_000_000
BASE_DATE = date(2024, 1, 2)

KINDS = [EventKind.BUY, EventKind.SELL, EventKind.DIVIDEND, EventKind.BONUS, EventKind.RIGHTS]
CODES = ["600000", "000001"]
SETTINGS = settings(
    max_examples=60,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)


@st.composite
def event_batches(draw, *, corporate_actions: bool = True) -> list[FifoEvent]:
    count = draw(st.integers(min_value=0, max_value=24))
    events: list[FifoEvent] = []
    for index in range(count):
        code = draw(st.sampled_from(CODES))
        day = BASE_DATE + timedelta(days=draw(st.integers(min_value=0, max_value=40)))
        kinds = KINDS if corporate_actions else [EventKind.BUY, EventKind.SELL]
        kind = draw(st.sampled_from(kinds))
        quantity = draw(st.integers(min_value=1, max_value=100)) * SHARE
        unit_price = draw(st.integers(min_value=1, max_value=5_000))  # 分/股
        amount = draw(st.integers(min_value=0, max_value=200_000))
        tax = draw(st.integers(min_value=0, max_value=amount))
        ratio_units = draw(st.sampled_from([0, 10_000_000, 30_000_000, 50_000_000, 100_000_000]))
        common: dict[str, object] = {
            "trade_id": f"t{index}",
            "trade_date": day,
            "kind": kind,
            "security_code": code,
            "security_name": f"证券{code}",
            "line_no": index,
        }
        if kind is EventKind.BUY:
            events.append(
                FifoEvent(
                    quantity_units=quantity,
                    price_units=unit_price * 1_000,
                    amount_units=-(quantity * unit_price // SHARE),
                    **common,  # type: ignore[arg-type]
                )
            )
        elif kind is EventKind.SELL:
            events.append(
                FifoEvent(
                    quantity_units=quantity,
                    price_units=unit_price * 1_000,
                    amount_units=quantity * unit_price // SHARE,
                    **common,  # type: ignore[arg-type]
                )
            )
        elif kind is EventKind.DIVIDEND:
            events.append(
                FifoEvent(
                    amount_units=amount,
                    dividend_tax_units=tax,
                    **common,  # type: ignore[arg-type]
                )
            )
        elif kind is EventKind.BONUS:
            events.append(
                FifoEvent(
                    quantity_units=quantity if ratio_units == 0 else 0,
                    ratio_units=ratio_units,
                    **common,  # type: ignore[arg-type]
                )
            )
        else:  # RIGHTS：缴款现金流为负
            events.append(
                FifoEvent(
                    quantity_units=quantity,
                    amount_units=-amount,
                    **common,  # type: ignore[arg-type]
                )
            )
    return events


def _bonus_added(total_quantity: int, event: FifoEvent) -> int:
    """送转新增数量（与生产实现同口径的**独立**整数公式，仅用于守恒对账）。"""
    if event.ratio_units > 0:
        return (total_quantity * event.ratio_units + RATIO_SCALE // 2) // RATIO_SCALE
    return event.quantity_units


def _replay_aggregates(events: list[FifoEvent]) -> dict[str, int]:
    """独立聚合重放：只跟踪每个证券的可用数量与已生效现金流。"""
    ordered = sorted(events, key=lambda event: event.stable_key)
    grouped: dict[str, list[FifoEvent]] = {}
    for event in ordered:
        grouped.setdefault(event.security_code, []).append(event)

    aggregates = {
        "buy_quantity": 0,
        "buy_cost": 0,
        "bonus_quantity": 0,
        "rights_quantity": 0,
        "rights_payment": 0,
        "matched_quantity": 0,
        "oversell_quantity": 0,
        "open_quantity": 0,
        "dividend_net": 0,
    }
    for security_events in grouped.values():
        available = 0
        for event in security_events:
            if event.kind is EventKind.BUY:
                if event.quantity_units <= 0:
                    continue
                available += event.quantity_units
                aggregates["buy_quantity"] += event.quantity_units
                aggregates["buy_cost"] += abs(event.amount_units)
            elif event.kind is EventKind.SELL:
                if event.quantity_units <= 0:
                    continue
                matched = min(available, event.quantity_units)
                available -= matched
                aggregates["matched_quantity"] += matched
                aggregates["oversell_quantity"] += event.quantity_units - matched
            elif event.kind is EventKind.DIVIDEND:
                if available > 0:
                    aggregates["dividend_net"] += event.amount_units - event.dividend_tax_units
            elif event.kind is EventKind.BONUS:
                if available > 0:
                    added = _bonus_added(available, event)
                    if added > 0:
                        available += added
                        aggregates["bonus_quantity"] += added
            elif event.kind is EventKind.RIGHTS:
                if event.quantity_units > 0 and available > 0:
                    available += event.quantity_units
                    aggregates["rights_quantity"] += event.quantity_units
                    aggregates["rights_payment"] += abs(event.amount_units)
        aggregates["open_quantity"] += available
    return aggregates


def _assert_no_negative_positions(events: list[FifoEvent]) -> None:
    result = build_rounds(events)
    for round_ in result.rounds:
        assert round_.quantity_units >= 0
        assert round_.cost_units >= 0
        assert round_.dividend_units >= 0
        assert round_.oversell_units >= 0
        # 周期语义不变量（open / closed 通用）：
        # - pnl 恒等式：pnl = proceeds - cost + dividend
        # - 有 fills 时 Σ fill.quantity == round.quantity_units（matched）
        assert round_.pnl_units == (
            round_.proceeds_units - round_.cost_units + round_.dividend_units
        )
        if round_.fills:
            assert sum(fill.quantity_units for fill in round_.fills) == round_.quantity_units
            assert sum(fill.pnl_units for fill in round_.fills) == round_.pnl_units
        # data_error 状态与 oversell_units 对齐
        if round_.status == "data_error":
            assert round_.data_error is True
        else:
            assert round_.data_error is False
    for lot in result.open_lots:
        assert lot.quantity_units > 0
        assert lot.cost_units >= 0
        assert lot.dividend_units >= 0


@SETTINGS
@given(event_batches())
def test_buy_sell_quantity_and_cost_conservation(events: list[FifoEvent]) -> None:
    result = build_rounds(events)
    aggregates = _replay_aggregates(events)

    # 数量守恒：买入 + 送转 + 配股 = 已配对 + 期末未平仓
    # （超卖来自缺失历史持仓，单独进 data_error；不计入该等式才能反映「无负持仓」）
    assert (
        aggregates["buy_quantity"] + aggregates["bonus_quantity"] + aggregates["rights_quantity"]
        == aggregates["matched_quantity"] + aggregates["open_quantity"]
    )

    matched_quantity = sum(
        round_.quantity_units
        for round_ in result.rounds  # 含 open / closed，都是已 FIFO 配对量
    )
    open_quantity = sum(lot.quantity_units for lot in result.open_lots)
    oversell_quantity = sum(
        error.quantity_units for error in result.data_errors if error.kind == "oversell"
    )
    assert matched_quantity == aggregates["matched_quantity"]
    assert open_quantity == aggregates["open_quantity"]
    assert oversell_quantity == aggregates["oversell_quantity"]

    # 成本守恒：Σ买入成本 + Σ配股缴款 = Σ closed round.cost（已消耗）+ Σ open_lot.cost（剩余）
    # （open round.cost 也是已消耗，已在 closed 里）
    closed_cost = sum(round_.cost_units for round_ in result.rounds)
    open_lot_cost = sum(lot.cost_units for lot in result.open_lots)
    assert aggregates["buy_cost"] + aggregates["rights_payment"] == closed_cost + open_lot_cost


@SETTINGS
@given(event_batches())
def test_dividend_conservation(events: list[FifoEvent]) -> None:
    result = build_rounds(events)
    aggregates = _replay_aggregates(events)
    # 新语义：round.dividend 仅含已消耗 lot 的股息，open_lots 含未消耗 lot 的股息
    # 两者之和 = 累计净股息
    consumed_dividend = sum(round_.dividend_units for round_ in result.rounds)
    open_lot_dividend = sum(lot.dividend_units for lot in result.open_lots)
    assert consumed_dividend + open_lot_dividend == aggregates["dividend_net"]
    # 股息聚合口 = closed + open（含 data_error 的已分配股息时单列）
    assert result.dividend_units <= consumed_dividend


@SETTINGS
@given(event_batches())
def test_no_negative_positions_and_pnl_identity(events: list[FifoEvent]) -> None:
    _assert_no_negative_positions(events)
    result = build_rounds(events)
    assert result.realized_pnl_units == sum(round_.pnl_units for round_ in result.closed_rounds)
    assert result.trading_pnl_units == sum(
        round_.pnl_units - round_.dividend_units for round_ in result.closed_rounds
    )
    for round_ in result.closed_rounds:
        assert round_.data_error is False
    for round_ in result.rounds:
        if round_.status == "data_error":
            assert round_.data_error is True


@SETTINGS
@given(event_batches(corporate_actions=False))
def test_invariants_buy_sell_only(events: list[FifoEvent]) -> None:
    _assert_no_negative_positions(events)
    result = build_rounds(events)
    aggregates = _replay_aggregates(events)
    assert aggregates["bonus_quantity"] == 0
    assert aggregates["rights_quantity"] == 0
    assert (
        aggregates["buy_quantity"] == aggregates["matched_quantity"] + aggregates["open_quantity"]
    )
    assert aggregates["buy_cost"] == sum(
        round_.cost_units
        for round_ in result.rounds  # 含 open / closed，都是已消耗 cost
    ) + sum(lot.cost_units for lot in result.open_lots)


@SETTINGS
@given(event_batches())
def test_result_is_independent_of_input_order(events: list[FifoEvent]) -> None:
    assert build_rounds(events) == build_rounds(list(reversed(events)))
    assert build_rounds(events) == build_rounds(sorted(events, key=lambda event: event.line_no))
