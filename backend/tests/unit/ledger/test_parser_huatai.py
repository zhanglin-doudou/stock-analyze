"""华泰解析器单测：列识别、日期格式/歧义、缺列报错、重复隔离、金额标度（无 float）。"""

from __future__ import annotations

import hashlib
import io
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from openpyxl import Workbook
from stock_contracts.clock import FrozenClock
from stock_contracts.enums import QuarantineReason, WarningCode
from stock_contracts.errors import ApiError, ErrorCode
from stock_ledger.parsers.columns import detect_date_format, map_columns
from stock_ledger.parsers.dedupe import dedupe_key
from stock_ledger.parsers.huatai import ParsedBill, parse_bill

CLOCK = FrozenClock(datetime(2026, 7, 1, 9, 0, tzinfo=UTC))

_HEADERS = [
    "成交日期",
    "证券代码",
    "证券名称",
    "买卖标志",
    "成交数量",
    "成交价格",
    "成交金额",
    "佣金",
    "印花税",
    "其他费",
    "资金余额",
    "摘要",
]

# 真实华泰账单形态：没有「买卖标志」列，金额列是「发生金额」带符号净额（已含费用）。
_REAL_BILL = Path(__file__).parents[2] / "api" / "华泰账单_mock.xlsx"
# 货币基金账单形态：同样没有「买卖标志」列，靠「申购确认/赎回确认」摘要定方向。
_FUND_BILL = Path(__file__).parents[2] / "api" / "华泰账单_货基_mock.xlsx"
# 真实对账单节选：摘要折行（[省心投]红\n利发放）、流水末尾「合计」+ 股票/基金持仓区块、重复行。
_STATEMENT_BILL = Path(__file__).parents[2] / "api" / "华泰账单_真实节选_mock.xlsx"


def _xlsx(headers: list[str], rows: list[list[str]]) -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(headers)
    for row in rows:
        sheet.append(row)
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def _parse(data: bytes, file_name: str = "bill.xlsx", **kwargs: object) -> ParsedBill:
    options: dict[str, object] = {"mapping": None, "date_format": None, "clock": CLOCK}
    options.update(kwargs)
    return parse_bill(data, file_name, **options)  # type: ignore[arg-type]


def _buy_row(**overrides: str) -> list[str]:
    row = {
        "成交日期": "20260304",
        "证券代码": "600519",
        "证券名称": "贵州茅台",
        "买卖标志": "证券买入",
        "成交数量": "100",
        "成交价格": "1680.00",
        "成交金额": "168000.00",
        "佣金": "42.00",
        "印花税": "0.00",
        "其他费": "0.00",
        "资金余额": "100000.00",
        "摘要": "证券买入",
    }
    row.update(overrides)
    return [row[header] for header in _HEADERS]


def test_column_recognition_and_scaling() -> None:
    bill = _parse(_xlsx(_HEADERS, [_buy_row()]))
    assert bill.columns["成交日期"] == "trade_date"
    assert bill.columns["证券代码"] == "security_code"
    assert bill.columns["买卖标志"] == "side"
    assert bill.unresolved == []
    row = bill.rows[0]
    assert row.trade_date == date(2026, 3, 4)
    assert row.quantity_units == 100_0000
    assert row.price_units == 1680_000000
    # 「成交金额」是毛额：统一口径换算成带符号净现金流 → 买入 = -(168000.00 + 42.00)
    assert row.amount_units == -168_042_00
    assert row.commission_units == 42_00
    assert row.cash_balance_units == 100000_00


def test_amount_scaling_handles_thousands_separator() -> None:
    bill = _parse(_xlsx(_HEADERS, [_buy_row(成交金额="1,234.56")]))
    assert bill.rows[0].amount_units == -(123456 + 42_00)


def test_signed_amount_header_used_directly_without_double_counting_fees() -> None:
    """「发生金额」原生带符号净额（已含费用）→ 直接采用，绝不再扣费用。"""
    headers = ["发生金额" if header == "成交金额" else header for header in _HEADERS]
    row = _buy_row(佣金="5.00", 印花税="0.00", 其他费="0.15")
    row[_HEADERS.index("成交金额")] = "-7415.15"
    bill = _parse(_xlsx(headers, [row]))
    r = bill.rows[0]
    assert r.side == "buy"
    assert r.amount_units == -7415_15  # 而不是 -(7415.15 + 5.15)


def test_gross_amount_converted_to_signed_net_cash_flow() -> None:
    """「成交金额」毛额 → 带符号净现金流：买入含费用为负、卖出扣费用为正。"""
    buy = _parse(
        _xlsx(
            _HEADERS,
            [_buy_row(成交金额="7410.00", 佣金="5.00", 印花税="0.00", 其他费="0.15")],
        )
    ).rows[0]
    assert buy.amount_units == -7415_15  # -(7410.00 + 5.15)
    sell = _parse(
        _xlsx(
            _HEADERS,
            [
                _buy_row(
                    买卖标志="证券卖出",
                    成交金额="7410.00",
                    佣金="5.00",
                    印花税="3.70",
                    其他费="0.15",
                )
            ],
        )
    ).rows[0]
    assert sell.amount_units == 7401_15  # 7410.00 - (5.00 + 3.70 + 0.15)


def test_negative_amount_in_gross_column_treated_as_signed_net() -> None:
    """毛额列出现负数只可能是已带符号的净额（毛额不可能为负）→ 按净额采用。"""
    row = _parse(_xlsx(_HEADERS, [_buy_row(成交金额="-800.00")])).rows[0]
    assert row.side == "buy"
    assert row.amount_units == -800_00


def test_gross_amount_with_unknown_side_has_no_sign() -> None:
    """毛额 + 方向不可判定 → 资金流向不可知，金额保守置 0 并在 assumptions 留痕。"""
    bill = _parse(
        _xlsx(
            _HEADERS,
            [_buy_row(买卖标志="未知标志", 摘要="未知业务XYZ", 成交金额="168000.00")],
        )
    )
    r = bill.rows[0]
    assert r.side == "unknown"
    assert r.amount_units == 0
    assert bill.assumptions.get("amount_unsigned_rows") == 1


def test_units_are_scale_integers_not_float() -> None:
    row = _parse(_xlsx(_HEADERS, [_buy_row()])).rows[0]
    for field in (
        row.quantity_units,
        row.price_units,
        row.amount_units,
        row.commission_units,
        row.stamp_tax_units,
        row.other_fee_units,
        row.cash_balance_units,
    ):
        assert isinstance(field, int) and not isinstance(field, bool)
    assert isinstance(Decimal(row.price_units).scaleb(-6), Decimal)


def test_date_format_yyyymmdd_detected() -> None:
    fmt, warnings = detect_date_format(["20260304", "20260305"])
    assert fmt == "YYYYMMDD"
    assert warnings == []


def test_date_format_ambiguous_dashed() -> None:
    fmt, warnings = detect_date_format(["2026-03-04", "2026-05-06"])
    assert fmt == "YYYY-MM-DD"
    assert WarningCode.DATE_FORMAT_AMBIGUOUS in warnings


def test_ambiguous_date_reported_in_bill() -> None:
    bill = _parse(
        _xlsx(_HEADERS, [_buy_row(成交日期="2026-03-04"), _buy_row(成交日期="2026-05-06")])
    )
    assert WarningCode.DATE_FORMAT_AMBIGUOUS in bill.warnings
    assert bill.rows[0].trade_date == date(2026, 3, 4)


def test_disambiguated_day_first_format() -> None:
    fmt, warnings = detect_date_format(["2026-19-10", "2026-20-11"])
    assert fmt == "YYYY-DD-MM"
    assert warnings == []


def test_missing_required_column_raises() -> None:
    # 买卖标志已改为非必需列，这里用仍然必需的「成交价格」缺失来触发缺列报错。
    headers = ["成交日期", "证券代码", "成交数量"]
    rows = [["20260304", "600519", "100"]]
    with pytest.raises(ApiError) as info:
        _parse(_xlsx(headers, rows))
    assert info.value.code == ErrorCode.IMPORT_COLUMNS_UNRESOLVED
    assert info.value.errors
    assert info.value.errors[0].field == "columns"


def test_duplicate_rows_quarantined() -> None:
    row = _buy_row()
    bill = _parse(_xlsx(_HEADERS, [row, list(row)]))
    assert len(bill.rows) == 1
    assert len(bill.quarantined) == 1
    assert bill.quarantined[0].reason_code == QuarantineReason.DUPLICATE_IN_FILE.value
    assert WarningCode.DUPLICATE_ROWS in bill.warnings


def test_legal_duplicates_keep_occurrence_idx() -> None:
    # 核心业务要素完全相同（日期/代码/方向/量/价/额/余额/账户），仅非核心的摘要不同：
    # 这不是重复行，应保留两条并各自分配 occurrence_idx（否则去重键会撞车）。
    first = _buy_row(摘要="证券买入")
    second = _buy_row(摘要="证券买入(补录)")
    bill = _parse(_xlsx(_HEADERS, [first, second]))
    assert len(bill.rows) == 2
    assert bill.quarantined == []
    assert {row.occurrence_idx for row in bill.rows} == {0, 1}
    assert bill.rows[0].dedupe_key != bill.rows[1].dedupe_key


def test_amount_derived_when_amount_column_missing() -> None:
    headers = [header for header in _HEADERS if header != "成交金额"]
    row = [
        value for header, value in zip(_HEADERS, _buy_row(), strict=True) if header != "成交金额"
    ]
    bill = _parse(_xlsx(headers, [row]))
    # 推导出毛额 100×1680.00 后同样要按方向换算成净现金流（买入含费用为负）。
    assert bill.rows[0].amount_units == -(168000_00 + 42_00)
    assert bill.assumptions.get("amount_derived_rows") == 1


def test_missing_cash_balance_warning() -> None:
    headers = [header for header in _HEADERS if header != "资金余额"]
    row = [
        value for header, value in zip(_HEADERS, _buy_row(), strict=True) if header != "资金余额"
    ]
    bill = _parse(_xlsx(headers, [row]))
    assert WarningCode.MISSING_CASH_BALANCE in bill.warnings
    assert bill.rows[0].cash_balance_available is False
    assert bill.rows[0].cash_balance_units is None


def test_first_row_not_buy_warning() -> None:
    bill = _parse(_xlsx(_HEADERS, [_buy_row(买卖标志="证券卖出")]))
    assert WarningCode.FIRST_ROW_NOT_BUY in bill.warnings
    assert bill.rows[0].side == "sell"


def test_unsupported_file_rejected() -> None:
    with pytest.raises(ApiError) as info:
        _parse(b"not a bill", file_name="bill.txt")
    assert info.value.code == ErrorCode.IMPORT_UNSUPPORTED_FILE


def test_side_column_absent_not_required() -> None:
    # 「买卖标志」列缺失不再报缺列；方向回退摘要识别（证券买入 → buy）。
    headers = [header for header in _HEADERS if header != "买卖标志"]
    row = [
        value
        for header, value in zip(_HEADERS, _buy_row(摘要="证券买入"), strict=True)
        if header != "买卖标志"
    ]
    bill = _parse(_xlsx(headers, [row]))
    assert "买卖标志" not in bill.columns
    assert bill.rows[0].side == "buy"


def test_side_from_summary_sell() -> None:
    bill = _parse(_xlsx(_HEADERS, [_buy_row(买卖标志="", 摘要="证券卖出")]))
    assert bill.rows[0].side == "sell"


def test_side_unknown_when_nothing_matches() -> None:
    # 买卖标志与摘要都无法识别 → 回落 unknown，且该行不再被隔离。
    bill = _parse(_xlsx(_HEADERS, [_buy_row(买卖标志="未知标志", 摘要="未知业务XYZ")]))
    assert len(bill.rows) == 1
    assert bill.quarantined == []
    assert bill.rows[0].side == "unknown"


def test_non_trade_bank_transfer_in() -> None:
    """银行转存：无证券代码，资金流入 → side=sell，代码占位 CASH。"""
    row = _buy_row(
        证券代码="",
        证券名称="",
        买卖标志="",
        摘要="银行转存",
        成交金额="188.58",
        佣金="0.00",
    )
    bill = _parse(_xlsx(_HEADERS, [row]))
    assert len(bill.rows) == 1
    assert bill.quarantined == []
    r = bill.rows[0]
    assert r.security_code == "CASH"
    assert r.security_name == "银行转存"
    assert r.side == "sell"
    # 毛额 188.58、零费用 → 净现金流 +188.58（资金流入为正）。
    assert r.amount_units == 188_58


def test_non_trade_bank_transfer_out() -> None:
    """银行转取：无证券代码，资金流出 → side=buy。"""
    row = _buy_row(证券代码="", 证券名称="", 买卖标志="", 摘要="银行转取", 成交金额="-800.00")
    bill = _parse(_xlsx(_HEADERS, [row]))
    assert len(bill.rows) == 1
    assert bill.quarantined == []
    r = bill.rows[0]
    assert r.security_code == "CASH"
    assert r.security_name == "银行转取"
    assert r.side == "buy"
    assert r.amount_units == -800_00


def test_non_trade_dividend_with_code() -> None:
    """股息入帐：有证券代码，资金流入 → side=sell。"""
    row = _buy_row(
        证券代码="603886",
        证券名称="元祖股份",
        买卖标志="",
        摘要="股息入帐",
        成交数量="0",
        成交价格="0.00",
        成交金额="1700.00",
        佣金="0.00",
    )
    bill = _parse(_xlsx(_HEADERS, [row]))
    assert len(bill.rows) == 1
    assert bill.quarantined == []
    r = bill.rows[0]
    assert r.security_code == "603886"
    assert r.security_name == "元祖股份"
    assert r.side == "sell"
    assert r.amount_units == 1700_00


def test_non_trade_interest() -> None:
    """利息归本：无证券代码，资金流入 → side=sell。"""
    row = _buy_row(
        证券代码="", 证券名称="", 买卖标志="", 摘要="利息归本", 成交金额="0.35", 佣金="0.00"
    )
    bill = _parse(_xlsx(_HEADERS, [row]))
    assert len(bill.rows) == 1
    assert bill.quarantined == []
    r = bill.rows[0]
    assert r.security_code == "CASH"
    assert r.side == "sell"
    assert r.amount_units == 35


def test_non_trade_dividend_tax_with_newline() -> None:
    """股息红利税补缴（含换行符）：无证券代码，资金流出 → side=buy。"""
    row = _buy_row(
        证券代码="", 证券名称="", 买卖标志="", 摘要="股息红利税\n补缴", 成交金额="-60.00"
    )
    bill = _parse(_xlsx(_HEADERS, [row]))
    assert len(bill.rows) == 1
    assert bill.quarantined == []
    r = bill.rows[0]
    assert r.security_code == "CASH"
    assert r.side == "buy"
    assert r.amount_units == -60_00


def test_zero_amount_rows_quarantined_as_invalid_amount() -> None:
    """佣金/印花税/其他费/发生金额/资金余额全为 0 → 隔离为 INVALID_AMOUNT，不入明细。"""
    for summary in ("转存管转入", "转存管转出"):
        row = _buy_row(
            证券代码="",
            证券名称="",
            买卖标志="",
            摘要=summary,
            成交数量="0",
            成交价格="0.00",
            成交金额="0.00",
            佣金="0.00",
            资金余额="0.00",
        )
        bill = _parse(_xlsx(_HEADERS, [row]))
        assert bill.rows == []
        assert len(bill.quarantined) == 1
        assert bill.quarantined[0].reason_code == QuarantineReason.INVALID_AMOUNT.value
        # 隔离行仍保留原始内容，供人工复核。
        assert bill.quarantined[0].raw["摘要"] == summary


def test_zero_amount_precedes_duplicate_quarantine() -> None:
    """全零行即使完全重复，也统一记 INVALID_AMOUNT（金额校验先于重复判定）。"""
    row = _buy_row(
        证券代码="",
        证券名称="",
        买卖标志="",
        摘要="银行转存",
        成交金额="0.00",
        佣金="0.00",
        资金余额="0.00",
    )
    bill = _parse(_xlsx(_HEADERS, [row, list(row)]))
    assert bill.rows == []
    assert {q.reason_code for q in bill.quarantined} == {QuarantineReason.INVALID_AMOUNT.value}


def test_real_bill_amount_is_signed_net_cash_flow() -> None:
    """真实账单（177 行中 2 行全零）：「发生金额」直接入 amount_units，买入负/卖出正、已含费用。"""
    bill = _parse(_REAL_BILL.read_bytes(), file_name=_REAL_BILL.name)
    assert len(bill.rows) == 175
    assert {q.reason_code for q in bill.quarantined} == {QuarantineReason.INVALID_AMOUNT.value}
    # 金额列原生带符号 → 不走毛额换算（否则会重复扣费产生假断链）。
    assert "amount_derived_rows" not in bill.assumptions
    assert "amount_unsigned_rows" not in bill.assumptions

    by_line = {row.line_no: row for row in bill.rows}
    buy = by_line[15]
    assert buy.side == "buy"
    # 200 股 × 37.05 = 7410.00 毛额；佣金 5.00 + 其他费 0.15 → 净额 -7415.15。
    assert buy.amount_units == -7415_15
    sell = by_line[17]
    assert sell.side == "sell"
    assert sell.amount_units == 7031_82

    # 全零的「转存管转入/转出」行已隔离，剩余行方向全为已知。
    assert all(row.side in {"buy", "sell"} for row in bill.rows)
    for row in bill.rows:
        if row.side == "buy":
            assert row.amount_units < 0, row.line_no
        elif row.side == "sell":
            assert row.amount_units > 0, row.line_no


def test_money_fund_redemption_confirmation_not_quarantined() -> None:
    """货币基金账单（无「买卖标志」列）：申赎确认按摘要定方向，赎回确认不能被隔离。"""
    bill = _parse(_FUND_BILL.read_bytes(), file_name=_FUND_BILL.name)
    assert len(bill.rows) == 6
    # 全零金额的「红利发放」（份额再投）按新规则隔离 INVALID_AMOUNT。
    assert [q.line_no for q in bill.quarantined] == [19]
    assert bill.quarantined[0].reason_code == QuarantineReason.INVALID_AMOUNT.value

    by_line = {row.line_no: row for row in bill.rows}
    redeem = by_line[17]
    assert redeem.summary == "赎回确认"
    assert redeem.side == "sell"
    assert redeem.quantity_units == 116295_3800
    # 「发生金额」原生带符号 → 直接采用，不翻号、不重复扣费。
    assert redeem.amount_units == 116295_38

    subscribe = by_line[18]
    assert subscribe.summary == "申购确认"
    assert subscribe.side == "buy"
    assert subscribe.amount_units == -106708_53

    # 股东账号在解析入口即脱敏：夹具里账号列为空 → 落库/隔离 raw 均不出现明文。
    assert all(row.account_masked is None for row in bill.rows)


def test_statement_stops_at_total_row_before_holdings_sections() -> None:
    """流水明细「合计」之后的股票/基金持仓、声明区块不是交易行 → 不进隔离区。"""
    bill = _parse(_STATEMENT_BILL.read_bytes(), file_name=_STATEMENT_BILL.name)
    # 10 条明细里 5 条五金额全 0（3 条红利再投 + 转托管确认 + 证券清理）→ INVALID_AMOUNT。
    assert len(bill.rows) == 5
    assert [q.line_no for q in bill.quarantined] == [17, 18, 19, 20, 25]
    assert {q.reason_code for q in bill.quarantined} == {QuarantineReason.INVALID_AMOUNT.value}
    # 「合计」在夹具第 27 行；持仓/声明区一行都不应被解析，更不能当 INVALID_ROW 隔离。
    assert max(row.line_no for row in bill.rows) == 26
    assert all(q.reason_code != QuarantineReason.INVALID_ROW.value for q in bill.quarantined)


def test_wrapped_summary_newline_normalized_for_side_and_parse() -> None:
    """摘要被券商折行（[省心投]申\\n购确认 / 新股申购确\\n认缴款）仍能识别方向。"""
    bill = _parse(_STATEMENT_BILL.read_bytes(), file_name=_STATEMENT_BILL.name)
    by_line = {row.line_no: row for row in bill.rows}
    subscribe = by_line[21]
    assert subscribe.summary == "[省心投]申\n购确认"
    assert subscribe.side == "buy"
    # 打新缴款摘要同样含「申购确认」，但方向仍是买入（分类由 new_stock 规则兜底）。
    ipo_payment = by_line[23]
    assert ipo_payment.summary == "新股申购确\n认缴款"
    assert ipo_payment.side == "buy"
    assert ipo_payment.amount_units == -26630_00
    # 托管转出发生金额 0 但资金余额非 0 → 保留为 unknown 方向；证券清理全零 → 隔离。
    assert by_line[22].side == "unknown"
    quarantined_lines = {q.line_no: q for q in bill.quarantined}
    assert quarantined_lines[25].reason_code == QuarantineReason.INVALID_AMOUNT.value


def test_money_fund_balance_running_with_opening_inferred_assumption() -> None:
    """货基行逐行累计余额（份额 × 1.0000 → 金额分）。

    无持仓锚点且账单内有净赎回：期初按「-min(累计净额)」反推下界（赎回行自证期初持仓），
    余额全程 ≥ 0，并写 assumptions["money_fund_opening_inferred"] 留痕。
    """
    bill = _parse(_FUND_BILL.read_bytes(), file_name=_FUND_BILL.name)
    by_line = {row.line_no: row for row in bill.rows}
    # 折行名称（华泰紫金天\n天发货币）仍识别为货基；其余股票/转账行为 NULL。
    # 期初下界 = 116307.65（最小累计净额 -116307.65 的反推），各行余额 = 旧值 + 期初。
    assert by_line[17].money_fund_balance_units == 12_27
    assert by_line[18].money_fund_balance_units == 106720_80
    assert by_line[20].money_fund_balance_units == 0
    assert by_line[23].money_fund_balance_units == 5668_32
    assert by_line[21].money_fund_balance_units is None
    assert by_line[22].money_fund_balance_units is None
    assert bill.assumptions["money_fund_opening_inferred"] == {"940018": 116307_65}
    assert "money_fund_opening_zero" not in bill.assumptions


def test_money_fund_balance_anchored_by_fund_holding_section() -> None:
    """真实节选：基金持仓区块给出期末锚点 51859.86，赎回行反推期初 100999.18。"""
    bill = _parse(_STATEMENT_BILL.read_bytes(), file_name=_STATEMENT_BILL.name)
    by_line = {row.line_no: row for row in bill.rows}
    # 卖出 49139.32 份后余额 = 期末参考市值 51859.86。
    assert by_line[26].money_fund_balance_units == 51859_86
    assert by_line[26].quantity_units == 49139_32_00
    # 期初 = 51859.86 − (−49139.32) = 100999.18（由锚点反推，无负余额）。
    opening = by_line[26].money_fund_balance_units - (
        -by_line[26].quantity_units * by_line[26].price_units // 10**8
    )
    assert opening == 100999_18
    # 202308 无持仓锚点 → 期初 0，余额 = 买入份额 10000.00。
    assert by_line[21].money_fund_balance_units == 10000_00
    assert bill.assumptions["money_fund_opening_zero"] == ["202308"]


def test_money_fund_recognized_by_price_one_and_zero_fees() -> None:
    """名称不含「货币」：买卖价格 = 1.000 且佣金/印花税/其他费全 0 → 按货基余额口径。"""
    bill = _parse(
        _xlsx(
            _HEADERS,
            [
                _buy_row(
                    证券代码="511880",
                    证券名称="银华日利",
                    成交价格="1.000",
                    成交数量="10000",
                    成交金额="10000.00",
                    佣金="0.00",
                    资金余额="50000.00",
                )
            ],
        )
    )
    row = bill.rows[0]
    assert row.money_fund_balance_units == 10000_00
    assert bill.assumptions["money_fund_opening_zero"] == ["511880"]


def test_price_one_with_fees_is_not_money_fund() -> None:
    """价格为 1 但有费用 → 仍按普通标的处理（不写货基余额）。"""
    bill = _parse(
        _xlsx(
            _HEADERS,
            [
                _buy_row(
                    证券代码="511880",
                    证券名称="银华日利",
                    成交价格="1.000",
                    成交数量="10000",
                    成交金额="10000.00",
                    佣金="1.00",
                    资金余额="50000.00",
                )
            ],
        )
    )
    assert bill.rows[0].money_fund_balance_units is None


def test_money_fund_price_one_sell_reduces_balance() -> None:
    """价格 1 零费用的卖出行同样识别为货基，余额按份额减少。"""
    bill = _parse(
        _xlsx(
            _HEADERS,
            [
                _buy_row(
                    证券代码="511880",
                    证券名称="银华日利",
                    成交价格="1.000",
                    成交数量="10000",
                    成交金额="10000.00",
                    佣金="0.00",
                    资金余额="50000.00",
                ),
                _buy_row(
                    证券代码="511880",
                    证券名称="银华日利",
                    买卖标志="证券卖出",
                    摘要="证券卖出",
                    成交价格="1.000",
                    成交数量="2000",
                    成交金额="2000.00",
                    佣金="0.00",
                    资金余额="52000.00",
                ),
            ],
        )
    )
    assert bill.rows[0].money_fund_balance_units == 10000_00
    assert bill.rows[1].money_fund_balance_units == 8000_00


def test_dedupe_key_ignores_money_fund_balance() -> None:
    """改余额不改去重键：货基余额是推演产物，不是账单原生字段。"""
    from dataclasses import replace

    bill = _parse(_FUND_BILL.read_bytes(), file_name=_FUND_BILL.name)
    row = next(item for item in bill.rows if item.money_fund_balance_units is not None)
    other = replace(row, money_fund_balance_units=(row.money_fund_balance_units or 0) + 1)
    assert dedupe_key(row) == dedupe_key(other)


def test_blank_code_unknown_summary_quarantined() -> None:
    """证券代码为空且摘要不属于非交易类 → 仍隔离（数据缺失保护）。"""
    row = _buy_row(证券代码="", 证券名称="", 买卖标志="", 摘要="未知业务XYZ")
    bill = _parse(_xlsx(_HEADERS, [row]))
    assert bill.rows == []
    assert len(bill.quarantined) == 1
    assert bill.quarantined[0].reason_code == QuarantineReason.INVALID_ROW.value


def test_missing_security_code_quarantined() -> None:
    bill = _parse(_xlsx(_HEADERS, [_buy_row(证券代码="")]))
    assert bill.rows == []
    assert bill.quarantined[0].reason_code == QuarantineReason.INVALID_ROW.value


def test_csv_parsing() -> None:
    header_line = ",".join(_HEADERS)
    data_line = ",".join(_buy_row())
    bill = _parse(f"{header_line}\n{data_line}\n".encode("utf-8-sig"), file_name="bill.csv")
    assert bill.columns["成交日期"] == "trade_date"
    assert bill.rows[0].amount_units == -(168000_00 + 42_00)


def test_map_columns_reports_unresolved_headers() -> None:
    result = map_columns(["成交日期", "乱七八糟列", "买卖标志"])
    assert result.columns["成交日期"] == "trade_date"
    assert "乱七八糟列" in result.unresolved


def test_mapping_override_resolves_custom_header() -> None:
    headers = ["日期", "代码", "标志", "数量", "价格"]
    mapping = {
        "日期": "trade_date",
        "代码": "security_code",
        "标志": "side",
        "数量": "quantity",
        "价格": "price",
    }
    bill = _parse(
        _xlsx(headers, [["20260304", "600519", "证券买入", "100", "1680.00"]]),
        mapping=mapping,
    )
    assert bill.columns["日期"] == "trade_date"
    assert bill.rows[0].security_code == "600519"


def test_account_stored_as_md5_digest() -> None:
    # 股东账号只落 MD5 摘要，不落明文。
    headers = [*_HEADERS, "股东账号"]
    row = [*_buy_row(), "0327201469"]
    bill = _parse(_xlsx(headers, [row]))
    account_masked = bill.rows[0].account_masked
    assert account_masked == hashlib.md5(b"0327201469").hexdigest()
    assert len(account_masked) == 32


def test_blank_account_stays_none() -> None:
    # 空账号保持 None：否则所有空账号同哈希，去重键会把不同行误判为重复。
    headers = [*_HEADERS, "股东账号"]
    row = [*_buy_row(), ""]
    bill = _parse(_xlsx(headers, [row]))
    assert bill.rows[0].account_masked is None


def test_quarantine_raw_account_already_hashed() -> None:
    # 隔离行的 raw 里账号列也必须是 MD5，不落明文。
    headers = [*_HEADERS, "股东账号"]
    # 证券代码为空 → 触发 INVALID_ROW 隔离，但隔离前 raw 已被统一抹除。
    row = [*_buy_row(证券代码=""), "0327201469"]
    bill = _parse(_xlsx(headers, [row]))
    assert bill.rows == []
    assert len(bill.quarantined) == 1
    raw = bill.quarantined[0].raw
    assert raw["股东账号"] == hashlib.md5(b"0327201469").hexdigest()
    assert raw["股东账号"] != "0327201469"


# ── 零金额信息行隔离（ZERO_AMOUNT_INFO，设计 2026-10-29 D1）──────────────────


def _info_row(**overrides: str) -> list[str]:
    """零金额纯通知行：无金额、无资金余额、无明确买卖方向。"""
    row = _buy_row(
        证券代码="001237",
        证券名称="惠康科技",
        买卖标志="",
        成交数量="0",
        成交价格="0",
        成交金额="0",
        佣金="0",
        印花税="0",
        其他费="0",
        资金余额="",
        摘要="申购中签",
    )
    values = dict(zip(_HEADERS, row, strict=True))
    values.update(overrides)
    return [values[header] for header in _HEADERS]


@pytest.mark.parametrize("keyword", ["申购中签", "新股入账", "托管转出", "托管转入"])
def test_zero_amount_info_rows_quarantined(keyword: str) -> None:
    bill = _parse(_xlsx(_HEADERS, [_info_row(摘要=keyword)]))
    assert bill.rows == []
    assert len(bill.quarantined) == 1
    assert bill.quarantined[0].reason_code == QuarantineReason.ZERO_AMOUNT_INFO.value


def test_zero_amount_info_folded_summary_quarantined() -> None:
    # 摘要折行（券商导出常见）归一化后同样命中关键词。
    bill = _parse(_xlsx(_HEADERS, [_info_row(摘要="申购中\n签")]))
    assert [q.reason_code for q in bill.quarantined] == [QuarantineReason.ZERO_AMOUNT_INFO.value]


def test_zero_amount_info_with_amount_kept() -> None:
    """同类摘要但有真实金额（中签缴款 -1000）→ 正常进明细，不隔离。"""
    bill = _parse(
        _xlsx(
            _HEADERS,
            [_info_row(买卖标志="证券买入", 成交金额="-1000.00", 摘要="申购中签")],
        )
    )
    assert bill.quarantined == []
    assert len(bill.rows) == 1
    assert bill.rows[0].amount_units == -1000_00


def test_zero_amount_info_with_cash_balance_kept() -> None:
    """摘要命中关键词但带资金余额（对账锚点）→ 保留，不隔离。"""
    bill = _parse(_xlsx(_HEADERS, [_info_row(资金余额="5000.00", 摘要="新股入账")]))
    assert bill.quarantined == []
    assert len(bill.rows) == 1
    assert bill.rows[0].cash_balance_units == 5000_00


def test_zero_amount_non_keyword_row_not_newly_quarantined() -> None:
    """无金额无余额但摘要不命中关键词 → 不落入 ZERO_AMOUNT_INFO（维持原口径）。"""
    bill = _parse(_xlsx(_HEADERS, [_info_row(摘要="其他业务")]))
    assert bill.quarantined == []
    assert len(bill.rows) == 1


# ── 可转债数量口径一致性 WARN（设计 2026-10-29 D2）──────────────────────────


def _cbond_row(**overrides: str) -> list[str]:
    row = _buy_row(
        证券代码="754019",
        证券名称="曙26发债",
        买卖标志="证券买入",
        成交数量="1",
        成交价格="100.00",
        成交金额="-1000.00",
        摘要="申购缴款",
    )
    values = dict(zip(_HEADERS, row, strict=True))
    values.update(overrides)
    return [values[header] for header in _HEADERS]


def test_cbond_consistent_row_no_warning() -> None:
    """1 手 × 10 张 × 100 元/张 = 1000 元，与发生金额一致 → 无 WARN。"""
    bill = _parse(_xlsx(_HEADERS, [_cbond_row()]))
    assert len(bill.rows) == 1
    assert WarningCode.CBOND_AMOUNT_INCONSISTENT not in bill.warnings
    assert "cbond_amount_inconsistent_rows" not in bill.assumptions


@pytest.mark.parametrize(
    ("code", "name"),
    [("754019", "曙26发债"), ("113050", "曙26转债")],
)
def test_cbond_inconsistent_row_warns(code: str, name: str) -> None:
    """发债代码（按名称识别）与上市转债代码（按代码段识别）偏差 >2% 都 WARN。"""
    bill = _parse(_xlsx(_HEADERS, [_cbond_row(证券代码=code, 证券名称=name, 成交金额="-5000.00")]))
    assert len(bill.rows) == 1  # 不阻断
    assert WarningCode.CBOND_AMOUNT_INCONSISTENT in bill.warnings
    assert bill.assumptions["cbond_amount_inconsistent_rows"] == 1


def test_cbond_tolerance_covers_fees() -> None:
    """金额含少量费用（-1000.50，偏差 0.05% ≤ 2%）→ 不误报。"""
    bill = _parse(_xlsx(_HEADERS, [_cbond_row(成交金额="-1000.50")]))
    assert WarningCode.CBOND_AMOUNT_INCONSISTENT not in bill.warnings


def test_non_bond_row_not_checked() -> None:
    """普通股票行不走可转债校验（600519 量价额自洽口径不同）。"""
    bill = _parse(_xlsx(_HEADERS, [_buy_row()]))
    assert WarningCode.CBOND_AMOUNT_INCONSISTENT not in bill.warnings
