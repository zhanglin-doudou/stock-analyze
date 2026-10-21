"""余额链口径回归：合成夹具（「成交金额」毛额 + 费用单列）换算后必须 0 断链。

口径：`Trade.amount_units` = 带符号净现金流（含费用），对账直接 `prev_balance + amount`。
真实账单（「发生金额」原生带符号）的 API 级 0 断链回归在
`backend/tests/api/test_imports_trades_jobs.py::test_api_026_real_bill_reconciles_with_zero_breaks`；
这里对合成夹具做纯函数复算，不依赖 PostgreSQL，作为解析器口径的快速守门。
"""

from __future__ import annotations

from datetime import UTC, datetime
from itertools import pairwise
from pathlib import Path

from stock_contracts.clock import FrozenClock
from stock_ledger.parsers.huatai import ParsedBill, ParsedRow, parse_bill

CLOCK = FrozenClock(datetime(2026, 7, 1, 9, 0, tzinfo=UTC))
_FIXTURE_BILL = Path(__file__).parents[4] / "fixtures" / "market" / "bill_huatai.xlsx"
_EPSILON_UNITS = 1


def _parse_fixture() -> ParsedBill:
    return parse_bill(
        _FIXTURE_BILL.read_bytes(),
        _FIXTURE_BILL.name,
        mapping=None,
        date_format=None,
        clock=CLOCK,
    )


def _breaks(rows: list[ParsedRow]) -> list[tuple[int, int, int]]:
    """复算余额链：`prev.cash_balance + current.amount == current.cash_balance`。

    返回 `(line_no, expected_units, actual_units)`；方向 unknown 的行与真实对账一致跳过。
    """
    breaks: list[tuple[int, int, int]] = []
    for previous, current in pairwise(rows):
        if previous.cash_balance_units is None or current.cash_balance_units is None:
            continue
        if current.side == "unknown":
            continue
        expected = previous.cash_balance_units + current.amount_units
        if abs(expected - current.cash_balance_units) > _EPSILON_UNITS:
            breaks.append((current.line_no, expected, current.cash_balance_units))
    return breaks


def test_synthetic_fixture_chain_has_zero_breaks() -> None:
    bill = _parse_fixture()
    assert bill.rows, "合成夹具应解析出明细行"
    # 夹具含重复行（解析阶段隔离），保留下来的行构成完整余额链。
    assert bill.quarantined
    assert _breaks(bill.rows) == []


def test_synthetic_fixture_amounts_follow_signed_convention() -> None:
    bill = _parse_fixture()
    assert "amount_derived_rows" not in bill.assumptions
    for row in bill.rows:
        if row.side == "buy":
            assert row.amount_units < 0, row.line_no
        elif row.side == "sell":
            assert row.amount_units > 0, row.line_no
