"""货币基金（现金等价物）识别与余额推演单测（2.3 口径）。

覆盖：名称强证据、1 元零费兜底证据、反证（ETF/行情品种/非 1 元/有费用）、
余额符号与期初反推，以及「货基不进 FIFO/回合」的端到端过滤。
"""

from __future__ import annotations

import uuid
from datetime import date

from stock_core.fifo import build_rounds
from stock_ledger.models import Trade
from stock_review.application.money_fund import (
    money_fund_balances,
    money_fund_codes,
    money_fund_oversells,
    money_fund_total_units,
    money_fund_value_units,
)
from stock_review.application.rebuild import trades_to_events

_USER = uuid.uuid4()


def _trade(
    *,
    trade_date: date = date(2026, 6, 1),
    security_code: str = "119924",
    security_name: str | None = None,
    category: str = "buy",
    quantity_units: int = 0,
    price_units: int = 1_000_000,
    amount_units: int = 0,
    commission_units: int = 0,
    side: str = "unknown",
) -> Trade:
    return Trade(
        id=f"trd_{uuid.uuid4().hex[:12]}",
        user_id=_USER,
        batch_id=None,
        trade_date=trade_date,
        security_code=security_code,
        security_name=security_name if security_name is not None else security_code,
        side=side,
        quantity_units=quantity_units,
        price_units=price_units,
        amount_units=amount_units,
        commission_units=commission_units,
        stamp_tax_units=0,
        other_fee_units=0,
        cash_balance_units=None,
        cash_balance_available=False,
        category=category,
        occurrence_idx=0,
        line_no=1,
        source="import",
        version=1,
        summary=category,
    )


# ── 识别 ──────────────────────────────────────────────────


def test_name_keywords_detect_money_fund() -> None:
    rows = [
        _trade(security_code="119924", security_name="平安证券现金宝", quantity_units=100_000_000),
        _trade(
            security_code="940018", security_name="华泰紫金天天发货币", quantity_units=100_000_000
        ),
    ]
    assert money_fund_codes(rows) == {"119924", "940018"}


def test_etf_name_is_not_a_cash_product() -> None:
    """场内货币 ETF 名称虽含「货币」，但按交易型基金处理（暂不自动识别）。"""
    rows = [
        _trade(
            security_code="511880",
            security_name="银华日利货币ETF",
            quantity_units=100_000_000,
            price_units=100_000_000,
        )
    ]
    assert money_fund_codes(rows) == frozenset()


def test_fallback_detects_pseudo_code_at_unit_price_without_fees() -> None:
    rows = [
        _trade(security_code="970172", quantity_units=10_000_000, amount_units=-1_000_00),
        _trade(
            security_code="970172",
            category="sell",
            quantity_units=4_000_000,
            amount_units=4_000_00,
        ),
    ]
    assert money_fund_codes(rows) == {"970172"}


def test_fallback_rejects_any_non_unit_price_or_fee() -> None:
    rows = [
        _trade(security_code="019547", quantity_units=10_000_000, amount_units=-1_000_00),
        _trade(
            security_code="019547",
            quantity_units=10_000_000,
            price_units=1_020_000,
            amount_units=-1_020_00,
        ),
    ]
    assert money_fund_codes(rows) == frozenset()

    fee_rows = [
        _trade(
            security_code="970172",
            quantity_units=10_000_000,
            amount_units=-1_005_00,
            commission_units=5_00,
        )
    ]
    assert money_fund_codes(fee_rows) == frozenset()


def test_fallback_rejects_quote_needing_symbol() -> None:
    rows = [_trade(security_code="600519", security_name="贵州茅台", quantity_units=1_000_000)]
    assert money_fund_codes(rows) == frozenset()


# ── 余额推演 ──────────────────────────────────────────────


def test_value_units_sign_and_dividend_reinvest() -> None:
    assert money_fund_value_units(_trade(quantity_units=10_000_000)) == 1_000_00
    assert money_fund_value_units(_trade(category="sell", quantity_units=10_000_000)) == -1_000_00
    # 红利再投：金额 0、份额增加 → 余额增加（计入收益，不计外部现金流）。
    assert (
        money_fund_value_units(
            _trade(category="dividend", quantity_units=2_000_000, amount_units=0)
        )
        == 200_00
    )


def test_broker_action_does_not_change_balance_or_holdings() -> None:
    """登记/过户类份额行不影响货基余额（避免与同日资金腿重复计入）。"""
    assert (
        money_fund_value_units(
            _trade(category="broker_action", quantity_units=10_000_000, amount_units=0)
        )
        == 0
    )
    rows = [
        _trade(category="buy", quantity_units=10_000_000, amount_units=-1_000_00),
        _trade(category="broker_action", quantity_units=10_000_000, amount_units=0),
    ]
    assert money_fund_balances(rows) == {"119924": 1_000_00}


def test_balances_fold_and_backsolve_first_redemption() -> None:
    rows = [
        _trade(
            trade_date=date(2026, 6, 2),
            category="sell",
            quantity_units=10_000_000,
            amount_units=1_000_00,
        ),
        _trade(
            trade_date=date(2026, 6, 3),
            quantity_units=4_000_000,
            amount_units=-400_00,
        ),
    ]
    # 首笔即赎回：反推期初 1000.00，赎回后为 0，再申购 400.00。
    assert money_fund_balances(rows, before=date(2026, 6, 2)) == {"119924": 1_000_00}
    assert money_fund_balances(rows, before=date(2026, 6, 3)) == {"119924": 0}
    assert money_fund_balances(rows) == {"119924": 400_00}
    assert money_fund_total_units(rows, before=date(2026, 6, 2)) == 1_000_00


def test_oversell_after_purchase_is_income_not_initial_balance() -> None:
    """申购后的赎回缺口是超卖（券商补记份额）：计入当日收益，不进期初资产。"""
    rows = [
        _trade(trade_date=date(2026, 9, 23), quantity_units=1_162_953_800, amount_units=-11_629_538),
        _trade(
            trade_date=date(2026, 9, 29),
            category="sell",
            quantity_units=1_162_953_800,
            amount_units=11_629_538,
        ),
        _trade(trade_date=date(2026, 9, 29), quantity_units=1_067_085_300, amount_units=-10_670_853),
        # 赎回 106,720.80 份 > 持有 106,708.53 份：超卖 12.27 份。
        _trade(
            trade_date=date(2026, 9, 30),
            category="sell",
            quantity_units=1_067_208_000,
            amount_units=10_672_080,
        ),
    ]
    # 期初不再被未来的超卖抬高；超卖前后的时点余额保持真实。
    assert money_fund_balances(rows, before=date(2026, 9, 1)) == {"119924": 0}
    assert money_fund_balances(rows, before=date(2026, 9, 30)) == {"119924": 106_708_53}
    assert money_fund_balances(rows) == {"119924": 0}
    # 份额正推记录超卖：日期 / 证券代码 / 超出份额。
    oversells = money_fund_oversells(rows)
    assert [(item.trade_date, item.security_code, item.quantity_units) for item in oversells] == [
        (date(2026, 9, 30), "119924", 122_700)
    ]


# ── 不进 FIFO / 回合 ──────────────────────────────────────


def test_money_fund_skipped_by_fifo_and_rounds() -> None:
    rows = [
        _trade(
            trade_date=date(2026, 6, 1),
            security_code="119924",
            quantity_units=10_000_000,
            amount_units=-1_000_00,
        ),
        _trade(
            trade_date=date(2026, 6, 2),
            security_code="600519",
            security_name="贵州茅台",
            quantity_units=1_000_000,
            price_units=1_700_000_000,
            amount_units=-1_700_00,
        ),
    ]
    result = build_rounds(trades_to_events(rows))
    assert {round_.security_code for round_ in result.rounds} == {"600519"}
    assert result.data_errors == ()
