"""桥接现金锚点单测：零金额份额行不能作为期初/期末余额锚点。

背景（2026-11-18）：用户 1234@dev.com 的 dashboard 降级，报「全历史桥接残差 101.15 元」。
根因是 2026-10-27 收盘前最后一笔是货基红利发放（金额 0、份额 > 0、账单「资金余额」列 0），
上一笔申购确认的券商现金是 101.15 元；期末锚点误取 0 后桥接正好差 101.15 元。
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from stock_ledger.models import Trade
from stock_review.application.bridge import _ending_cash, _initial_cash

_USER = uuid.uuid4()
_DAY = date(2026, 10, 27)


def cents(value: str) -> int:
    return int(Decimal(value) * Decimal(100))


def shares(value: str) -> int:
    return int(Decimal(value) * Decimal(10**4))


def _trade(
    *,
    trade_date: date,
    category: str | None,
    amount_units: int,
    cash_balance_units: int | None = None,
    quantity_units: int = 0,
    price_units: int = 0,
    security_code: str = "940018",
    security_name: str = "华泰紫金天天发货币",
    line_no: int = 1,
) -> Trade:
    return Trade(
        id=f"trd_{uuid.uuid4().hex[:12]}",
        user_id=_USER,
        batch_id=None,
        trade_date=trade_date,
        security_code=security_code,
        security_name=security_name,
        side="unknown",
        quantity_units=quantity_units,
        price_units=price_units,
        amount_units=amount_units,
        commission_units=0,
        stamp_tax_units=0,
        other_fee_units=0,
        cash_balance_units=cash_balance_units,
        cash_balance_available=cash_balance_units is not None,
        category=category,
        occurrence_idx=0,
        line_no=line_no,
        source="import",
        version=1,
        summary=category,
    )


def test_ending_cash_ignores_trailing_zero_amount_share_row() -> None:
    """期末锚点跳过「零金额 + 有份额」的货基红利行，取上一笔真实余额。"""
    trades = [
        _trade(
            trade_date=_DAY,
            category="buy",
            amount_units=-cents("1414.19"),
            cash_balance_units=cents("101.15"),
            quantity_units=shares("1414"),
            price_units=1_000_000,
            line_no=1,
        ),
        _trade(
            trade_date=_DAY,
            category="interest",
            amount_units=0,
            cash_balance_units=0,
            quantity_units=shares("407600"),
            price_units=1_000_000,
            line_no=2,
        ),
    ]
    assert _ending_cash(trades, _DAY, _DAY, initial_cash=0) == (cents("101.15"), "statement")


def test_ending_cash_derived_when_period_has_only_share_rows() -> None:
    """区间内只有零金额份额行时按现金流推导（期初 + 0），并让调用方降级。"""
    trades = [
        _trade(
            trade_date=_DAY,
            category="interest",
            amount_units=0,
            cash_balance_units=0,
            quantity_units=shares("407600"),
            price_units=1_000_000,
        )
    ]
    assert _ending_cash(trades, _DAY, _DAY, initial_cash=cents("101.15")) == (
        cents("101.15"),
        "derived",
    )


def test_initial_cash_back_solves_from_first_anchor_after_period() -> None:
    """区间当日首条是零金额份额行时，反推改用其后第一条真实余额行。"""
    trades = [
        _trade(
            trade_date=_DAY,
            category="interest",
            amount_units=0,
            cash_balance_units=0,
            quantity_units=shares("407600"),
            price_units=1_000_000,
            line_no=1,
        ),
        _trade(
            trade_date=_DAY,
            category="buy",
            amount_units=-cents("1414.19"),
            cash_balance_units=cents("101.15"),
            quantity_units=shares("1414"),
            price_units=1_000_000,
            line_no=2,
        ),
    ]
    # 期初 = 101.15 − (−1414.19) − 0 = 1515.34
    assert _initial_cash(trades, _DAY) == (cents("1515.34"), "back_solved")


def test_initial_cash_uses_last_anchor_before_period() -> None:
    """区间前最近一条锚点即期初现金；其后的零金额份额行不影响。"""
    trades = [
        _trade(
            trade_date=date(2026, 10, 24),
            category="sell",
            amount_units=cents("500.00"),
            cash_balance_units=cents("500.00"),
        ),
        _trade(
            trade_date=_DAY,
            category="interest",
            amount_units=0,
            cash_balance_units=0,
            quantity_units=shares("100"),
            price_units=1_000_000,
        ),
    ]
    assert _initial_cash(trades, _DAY) == (cents("500.00"), "statement")
