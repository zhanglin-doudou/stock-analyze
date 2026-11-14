"""导入对账链的纯函数单测：断行隔离、同日锚点跳过、首次导入头部重试。"""

from __future__ import annotations

from datetime import date

from stock_contracts.enums import QuarantineReason
from stock_contracts.errors import ErrorCode
from stock_ledger.application.chain import (
    ChainMode,
    ChainPlan,
    apply_chain_plan,
    walk_chain,
    walk_chain_with_head_retry,
)
from stock_ledger.parsers.huatai import ParsedBill, ParsedRow

D1 = date(2026, 4, 1)
D2 = date(2026, 4, 2)
D3 = date(2026, 4, 3)


def _row(
    line_no: int,
    amount_units: int,
    balance_units: int | None,
    *,
    day: date = D1,
    side: str = "buy",
) -> ParsedRow:
    return ParsedRow(
        line_no=line_no,
        trade_date=day,
        security_code="600001",
        security_name="测试",
        side=side,
        quantity_units=100,
        price_units=10_000,
        amount_units=amount_units,
        commission_units=0,
        stamp_tax_units=0,
        other_fee_units=0,
        cash_balance_units=balance_units,
        cash_balance_available=balance_units is not None,
        summary="证券买入",
        summary_code=None,
        account_masked=None,
        occurrence_idx=0,
        dedupe_key=f"k{line_no}",
        raw={"摘要": "证券买入"},
    )


def test_walk_chain_quarantines_from_break_to_tail() -> None:
    rows = [
        _row(1, -100, 9_900),
        _row(2, -100, 9_700),  # 断行：期望 9_800
        _row(3, -100, 9_800),  # 看似能接回，但锚点已不可信 → 一并隔离到链尾
    ]
    walk = walk_chain(rows, start_balance_units=10_000, epsilon_units=0)

    assert [row.line_no for row in walk.accepted] == [1]
    assert [item.line_no for item in walk.quarantined] == [2, 3]
    assert walk.quarantined[0].reason_code == QuarantineReason.RECONCILIATION_BREAK.value
    assert walk.quarantined[0].suggested_rule_id is None
    # 首条断行留痕：期望/实际/差额（分与定点字符串）
    assert walk.quarantined[0].raw["_chain_expected_units"] == "9800"
    assert walk.quarantined[0].raw["_chain_actual_units"] == "9700"
    assert walk.quarantined[0].raw["_chain_delta"] == "-1.00"
    assert "_chain_delta" not in walk.quarantined[1].raw
    assert walk.last_balance_units == 9_900


def test_walk_chain_skips_rows_before_same_day_anchor() -> None:
    rows = [
        _row(1, -100, 9_900),
        _row(2, -100, 9_800),
        _row(3, -100, 9_900),  # 锚点（同日第 2 行）之后的第 1 行
    ]
    walk = walk_chain(
        rows,
        start_balance_units=10_000,
        epsilon_units=0,
        skip_before=(D1, 2),
    )

    assert walk.skipped == 2
    assert [row.line_no for row in walk.accepted] == [3]
    assert walk.quarantined == []


def test_walk_chain_unknown_side_is_verified_but_not_anchored() -> None:
    rows = [
        _row(1, -100, 9_900, side="unknown"),
        _row(2, -100, 9_800),
    ]
    walk = walk_chain(rows, start_balance_units=10_000, epsilon_units=0)

    assert [row.line_no for row in walk.accepted] == [1, 2]
    assert walk.quarantined == []


def test_walk_chain_unknown_balance_cannot_absorb_gap() -> None:
    # 方向未知行的余额「凭空少了 5,000」也不能当锚点：缺口留到下一条可核对行暴露。
    rows = [
        _row(1, -100, 9_900),
        _row(2, -500, 4_900, side="unknown"),
        _row(3, 0, 4_900),
    ]
    walk = walk_chain(rows, start_balance_units=10_000, epsilon_units=0)

    assert [row.line_no for row in walk.accepted] == [1, 2]
    assert [item.line_no for item in walk.quarantined] == [3]
    # 期望 = 9,900 − 500 + 0 = 9,400；实际 4,900 → 差额 −45.00
    assert walk.quarantined[0].raw["_chain_delta"] == "-45.00"
    # 差额是相对「通过核对的行」算的，提示不能指向未定锚的 unknown 行（第 2 行）。
    assert walk.quarantined[0].raw["_chain_counterpart"] == "第 1 行"


def test_walk_chain_no_balance_rows_accumulate_amounts() -> None:
    rows = [
        _row(1, -100, 9_900),
        _row(2, -100, None),  # 无余额：金额并入待核对合计
        _row(3, -100, 9_700),
    ]
    walk = walk_chain(rows, start_balance_units=10_000, epsilon_units=0)

    assert [row.line_no for row in walk.accepted] == [1, 2, 3]
    assert walk.quarantined == []


def test_walk_chain_without_balance_rows_is_degraded_pass_through() -> None:
    rows = [_row(1, -100, None), _row(2, -100, None)]
    walk = walk_chain(rows, start_balance_units=None, epsilon_units=0)

    assert len(walk.accepted) == 2
    assert walk.quarantined == []


def test_head_retry_prefers_later_anchor_when_head_is_noise() -> None:
    # 第 1 行是噪声：余额 9_000 与后续真实链 10_000 → 9_900 → 9_800 对不上。
    rows = [
        _row(1, -500, 9_000),
        _row(2, -100, 9_900),
        _row(3, -100, 9_800),
    ]
    walk = walk_chain_with_head_retry(rows, epsilon_units=0)

    assert [row.line_no for row in walk.accepted] == [2, 3]
    assert [item.line_no for item in walk.quarantined] == [1]


def test_head_retry_ties_choose_earliest_candidate() -> None:
    rows = [_row(1, -100, 9_900), _row(2, -100, 9_700)]
    walk = walk_chain_with_head_retry(rows, epsilon_units=0)

    # 两个候选各接受 1 行，取最早（第 1 行）作锚点。
    assert [row.line_no for row in walk.accepted] == [1]
    assert [item.line_no for item in walk.quarantined] == [2]


def test_apply_chain_plan_only_keeps_accepted_rows() -> None:
    bill = ParsedBill(
        rows=[_row(1, -100, 9_900), _row(2, -100, 9_700)],
        quarantined=[],
        warnings=[],
        assumptions={},
        columns={},
        date_format=None,
    )
    plan = ChainPlan(
        mode=ChainMode.EMPTY,
        accepted=[bill.rows[0]],
        quarantined=[],
        coverage={"mode": "empty"},
    )
    apply_chain_plan(bill, plan)

    assert [row.line_no for row in bill.rows] == [1]
    assert bill.assumptions["coverage"] == {"mode": "empty"}


def test_chain_discontinuous_error_carries_line_and_delta() -> None:
    from stock_ledger.application.chain import _discontinuous

    error = _discontinuous(
        line_no=7,
        delta_units=-12_345,
        counterpart="库内首条账单",
        detail="断开",
    )
    assert error.code == ErrorCode.IMPORT_CHAIN_DISCONTINUOUS
    assert error.errors[0].field == "7"
    assert "-123.45" in error.errors[0].message
