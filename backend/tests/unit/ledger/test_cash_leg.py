"""资金腿归属归一单测：同日份额腿配对 → 货基兜底 → 现金兜底/报错。"""

from __future__ import annotations

import uuid
from datetime import date

import pytest
import stock_ledger.application.cash_leg as cash_leg_module
from stock_contracts.errors import ApiError, ErrorCode
from stock_ledger.application.cash_leg import attribute_cash_legs
from stock_ledger.repositories.trades import TradeRow

_DAY = date(2026, 10, 26)


def _row(
    *,
    trade_date: date = _DAY,
    code: str = "",
    name: str = "",
    category: str = "sell",
    quantity_units: int = 0,
    price_units: int = 0,
    amount_units: int = 0,
    cash_balance_units: int | None = None,
    line_no: int = 1,
) -> TradeRow:
    return TradeRow(
        trade_date=trade_date,
        security_code=code,
        security_name=name,
        side="sell" if category == "sell" else "buy",
        quantity_units=quantity_units,
        price_units=price_units,
        amount_units=amount_units,
        dedupe_key=f"key-{uuid.uuid4().hex}",
        category=category,
        cash_balance_units=cash_balance_units,
        line_no=line_no,
    )


def _cash_leg(amount_units: int, *, line_no: int = 1) -> TradeRow:
    return _row(
        code="CASH",
        category="sell",
        amount_units=amount_units,
        cash_balance_units=10_117,
        line_no=line_no,
    )


def _money_fund(code: str, name: str, balance_units: int, *, day: date) -> TradeRow:
    return _row(
        trade_date=day,
        code=code,
        name=name,
        category="buy",
        quantity_units=balance_units * 100,
        price_units=1_000_000,
        amount_units=-balance_units,
    )


class _Recorder:
    def __init__(self) -> None:
        self.events: list[tuple[str, str, dict]] = []

    def info(self, event: str, **kwargs) -> None:
        self.events.append(("info", event, kwargs))

    def warning(self, event: str, **kwargs) -> None:
        self.events.append(("warning", event, kwargs))


@pytest.fixture()
def recorder(monkeypatch) -> _Recorder:
    rec = _Recorder()
    monkeypatch.setattr(cash_leg_module, "log", rec)
    return rec


def test_pairs_same_day_share_leg(recorder: _Recorder) -> None:
    """同日份额腿可配对：代码/数量/单价写到资金腿上，不发 warning。"""
    history = [_money_fund("940018", "华泰紫金天天发货币", 20_000_000, day=date(2026, 10, 23))]
    leg = _cash_leg(17_999_900)
    share = _row(
        code="940018",
        name="华泰紫金天天发货币",
        category="broker_action",
        quantity_units=1_799_990_000,
        price_units=1_000_000,
        amount_units=0,
        line_no=2,
    )

    result = attribute_cash_legs([leg, share], history=history)

    assert result[0].security_code == "940018"
    assert result[0].quantity_units == 1_799_990_000
    assert result[0].price_units == 1_000_000
    assert result[0].amount_units == 17_999_900
    assert [(level, event) for level, event, _ in recorder.events] == [
        ("info", "import.cash_leg.share_leg_paired")
    ]
    _, _, fields = recorder.events[0]
    assert fields["security_code"] == "940018"
    assert fields["quantity_units"] == -1_799_990_000
    assert fields["amount_units"] == 17_999_900


def test_share_leg_on_other_day_is_not_paired(recorder: _Recorder) -> None:
    """跨日份额腿不参与配对：避免把别的日期的持仓/收益挪到资金腿当天。"""
    history = [_money_fund("940018", "华泰紫金天天发货币", 20_000_000, day=date(2026, 10, 23))]
    leg = _cash_leg(17_999_900)
    share = _row(
        trade_date=date(2026, 10, 27),
        code="940018",
        name="华泰紫金天天发货币",
        category="broker_action",
        quantity_units=1_799_990_000,
        price_units=1_000_000,
        amount_units=0,
        line_no=2,
    )

    result = attribute_cash_legs([leg, share], history=history)

    # 未配对 → 按货基持仓兜底；份额腿原样保留。
    assert [(level, event) for level, event, _ in recorder.events] == [
        ("info", "import.cash_leg.money_fund_adjusted")
    ]
    assert result[0].security_code == "940018"
    assert result[0].quantity_units == 1_799_990_000
    assert result[1] is share


def test_pair_rejected_when_shares_go_negative_falls_back(recorder: _Recorder) -> None:
    """扣减会为负 → 放弃配对（warning），转货基兜底并记 info。"""
    history = [
        _money_fund("940018", "华泰紫金天天发货币", 1_000_000, day=date(2026, 10, 23)),
        _money_fund("970172", "平安证券现金宝", 30_000_000, day=date(2026, 10, 23)),
    ]
    leg = _cash_leg(17_999_900)
    share = _row(
        code="940018",
        name="华泰紫金天天发货币",
        category="broker_action",
        quantity_units=1_799_990_000,
        price_units=1_000_000,
        amount_units=0,
        line_no=2,
    )

    result = attribute_cash_legs([leg, share], history=history)

    assert result[0].security_code == "970172"
    assert result[0].quantity_units == 1_799_990_000
    assert result[0].price_units == 1_000_000
    events = [(level, event) for level, event, _ in recorder.events]
    assert events == [
        ("warning", "import.cash_leg.share_leg_rejected"),
        ("info", "import.cash_leg.money_fund_adjusted"),
    ]
    assert recorder.events[0][2]["security_code"] == "940018"
    assert recorder.events[1][2]["security_code"] == "970172"


def test_multiple_money_funds_allocate_by_balance_desc(recorder: _Recorder) -> None:
    """多只货基按持仓份额从多到少依次扣减，拆成多行且金额守恒。"""
    history = [
        _money_fund("970172", "平安证券现金宝", 60_000, day=date(2026, 10, 23)),
        _money_fund("970173", "货币基金B", 40_000, day=date(2026, 10, 23)),
    ]
    leg = _cash_leg(80_000)

    result = attribute_cash_legs([leg], history=history)

    assert [row.security_code for row in result] == ["970172", "970173"]
    assert [row.quantity_units for row in result] == [6_000_000, 2_000_000]
    assert [row.amount_units for row in result] == [60_000, 20_000]
    assert result[0].dedupe_key != result[1].dedupe_key
    assert [event for _, event, _ in recorder.events] == [
        "import.cash_leg.money_fund_adjusted",
        "import.cash_leg.money_fund_adjusted",
    ]


def test_residual_falls_to_cash_balance(recorder: _Recorder) -> None:
    """货基不够的部分落现金余额；现金余额扣完仍为正 → 不报错。"""
    history = [_money_fund("970172", "平安证券现金宝", 10_000, day=date(2026, 10, 23))]
    leg = _cash_leg(30_000)
    leg.cash_balance_units = 50_000

    result = attribute_cash_legs([leg], history=history)

    assert [row.security_code for row in result] == ["970172", "CASH"]
    assert [row.amount_units for row in result] == [10_000, 20_000]
    assert result[1].quantity_units == 0
    assert result[1].price_units == 0


def test_negative_cash_balance_raises(recorder: _Recorder) -> None:
    """无货基可扣、现金余额也不够 → 对账错误，直接抛错而不是静默 clamp。"""
    leg = _cash_leg(30_000)
    leg.cash_balance_units = 10_000

    with pytest.raises(ApiError) as exc:
        attribute_cash_legs([leg])

    assert exc.value.code is ErrorCode.IMPORT_CHAIN_DISCONTINUOUS
    assert recorder.events == []
