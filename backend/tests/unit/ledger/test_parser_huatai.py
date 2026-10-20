"""华泰解析器单测：列识别、日期格式/歧义、缺列报错、重复隔离、金额标度（无 float）。"""

from __future__ import annotations

import hashlib
import io
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from openpyxl import Workbook
from stock_contracts.clock import FrozenClock
from stock_contracts.enums import QuarantineReason, WarningCode
from stock_contracts.errors import ApiError, ErrorCode
from stock_ledger.parsers.columns import detect_date_format, map_columns
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
    assert row.amount_units == 168000_00
    assert row.commission_units == 42_00
    assert row.cash_balance_units == 100000_00


def test_amount_scaling_handles_thousands_separator() -> None:
    bill = _parse(_xlsx(_HEADERS, [_buy_row(成交金额="1,234.56")]))
    assert bill.rows[0].amount_units == 123456


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
    assert bill.rows[0].amount_units == 168000_00
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


def test_missing_security_code_quarantined() -> None:
    bill = _parse(_xlsx(_HEADERS, [_buy_row(证券代码="")]))
    assert bill.rows == []
    assert bill.quarantined[0].reason_code == QuarantineReason.INVALID_ROW.value


def test_csv_parsing() -> None:
    header_line = ",".join(_HEADERS)
    data_line = ",".join(_buy_row())
    bill = _parse(f"{header_line}\n{data_line}\n".encode("utf-8-sig"), file_name="bill.csv")
    assert bill.columns["成交日期"] == "trade_date"
    assert bill.rows[0].amount_units == 168000_00


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
