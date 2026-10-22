"""`trades_to_events` 纯映射单测（INTERFACE §3.3 / §3.7）。

覆盖：买卖/股息/配股/费用/划转/利息/打新/送转识别/补差行/未知类别/空字段，
以及映射后的稳定序字段（line_no、summary、数量、价格、trade_id）。
"""

from __future__ import annotations

import uuid
from datetime import date

from stock_core.fifo import EventKind
from stock_ledger.models import Trade
from stock_review.application.rebuild import trades_to_events

USER_ID = uuid.UUID("00000000-0000-4000-8000-00000000000a")


def _trade(**overrides: object) -> Trade:
    """构造一条未落库的 Trade（显式给全映射用到的字段，避免默认值未 flush）。"""
    values: dict[str, object] = {
        "id": "trd_" + uuid.uuid4().hex[:12],
        "user_id": USER_ID,
        "trade_date": date(2026, 6, 30),
        "security_code": "600519",
        "security_name": "贵州茅台",
        "side": "buy",
        "quantity_units": 10_000_000,  # 1000 股（1e-4）
        "price_units": 10_000_000,  # 10.00 元（1e-6）
        "amount_units": -1_000_000,  # -10000.00 元（分，买入为负）
        "commission_units": 0,
        "stamp_tax_units": 0,
        "other_fee_units": 0,
        "summary": "证券买入",
        "category": "buy",
        "line_no": 3,
        "dedupe_key": "dk",
    }
    values.update(overrides)
    return Trade(**values)  # type: ignore[arg-type]


def test_buy_and_sell_map_to_fifo_kinds() -> None:
    buy = _trade(category="buy", side="buy", amount_units=-1_000_000)
    sell = _trade(category="sell", side="sell", amount_units=1_200_000, quantity_units=4_000_000)
    events = trades_to_events([buy, sell])

    assert [event.kind for event in events] == [EventKind.BUY, EventKind.SELL]
    assert events[0].amount_units == -1_000_000
    assert events[1].amount_units == 1_200_000
    assert events[0].trade_id == buy.id
    assert events[0].quantity_units == 10_000_000
    assert events[0].price_units == 10_000_000
    assert events[0].line_no == 3
    assert events[0].summary == "证券买入"
    # ledger 的 amount 已是净现金流，映射不做任何反号。
    assert events[1].trade_id == sell.id


def test_dividend_is_net_and_tax_row_stays_fee() -> None:
    dividend = _trade(
        category="dividend",
        side="buy",
        summary="红利入账",
        amount_units=50_000,
        quantity_units=0,
        price_units=0,
    )
    tax = _trade(
        category="fee",
        side="buy",
        summary="股息红利税补缴",
        amount_units=-10_000,
        quantity_units=0,
        price_units=0,
    )
    dividend_event, tax_event = trades_to_events([dividend, tax])

    assert dividend_event.kind is EventKind.DIVIDEND
    assert dividend_event.amount_units == 50_000
    # 红利税是独立 fee 行：DIVIDEND 不再带税，避免税被扣两次。
    assert dividend_event.dividend_tax_units == 0
    assert tax_event.kind is EventKind.FEE
    assert tax_event.amount_units == -10_000


def test_rights_payment_maps_to_rights() -> None:
    rights = _trade(
        category="rights",
        side="buy",
        summary="配股缴款",
        amount_units=-300_000,
        quantity_units=3_000_000,
    )
    (event,) = trades_to_events([rights])
    assert event.kind is EventKind.RIGHTS
    assert event.amount_units == -300_000
    assert event.quantity_units == 3_000_000


def test_bonus_by_summary_when_zero_amount_and_positive_quantity() -> None:
    for summary in ("送股入账", "转增股份", "10送转3"):
        trade = _trade(
            category="other",
            side="buy",
            summary=summary,
            amount_units=0,
            quantity_units=3_000_000,
        )
        (event,) = trades_to_events([trade])
        assert event.kind is EventKind.BONUS, summary
        # 比例缺失时按数量直接加股；送转成本不变由引擎保证。
        assert event.quantity_units == 3_000_000
        assert event.ratio_units == 0
        assert event.amount_units == 0


def test_zero_quantity_or_nonzero_amount_is_not_bonus() -> None:
    zero_qty = _trade(category="other", summary="送股入账", amount_units=0, quantity_units=0)
    with_cash = _trade(category="other", summary="送股入账", amount_units=100, quantity_units=100)
    events = trades_to_events([zero_qty, with_cash])
    assert [event.kind for event in events] == [EventKind.OTHER, EventKind.OTHER]


def test_other_categories_and_missing_side() -> None:
    trades = [
        _trade(category="interest", summary="利息归本", amount_units=1_234),
        _trade(category="fee", summary="佣金", amount_units=-500),
        _trade(category="transfer", summary="银行转证券", amount_units=10_000_000),
        _trade(category="new_stock", summary="新股申购", amount_units=-10_000),
        _trade(category="other", summary="证券清理", amount_units=0),
        _trade(category="other", summary="对账自动补差", side="missing", amount_units=88),
        _trade(category=None, summary=None, side="unknown", amount_units=0, line_no=None),
    ]
    kinds = [event.kind for event in trades_to_events(trades)]
    assert kinds == [
        EventKind.INTEREST,
        EventKind.FEE,
        EventKind.TRANSFER,
        EventKind.NEW_STOCK,
        EventKind.OTHER,
        EventKind.OTHER,
        EventKind.OTHER,
    ]
    # 空字段规范化：line_no 缺失 → 0、summary 缺失 → ""（稳定序需要确定性）。
    last = trades_to_events(trades)[-1]
    assert last.line_no == 0
    assert last.summary == ""


def test_empty_input_returns_empty_list() -> None:
    assert trades_to_events([]) == []


def test_amount_sign_passthrough_keeps_fifo_semantics() -> None:
    """映射必须是纯函数：同一 Trade 多次映射结果相等且不改原对象。"""
    trade = _trade()
    first = trades_to_events([trade])
    second = trades_to_events([trade])
    assert first == second
    assert trade.amount_units == -1_000_000
