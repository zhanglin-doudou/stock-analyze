"""v2 导入映射：列键/计划校验/建议/画像识别，以及与 legacy 解析的逐字段一致。"""

from __future__ import annotations

import io
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from openpyxl import Workbook
from stock_contracts.clock import FrozenClock
from stock_contracts.enums import WarningCode
from stock_contracts.errors import ApiError, ErrorCode
from stock_contracts.import_fields import FIELD_ALIASES, MERGEABLE_FIELDS
from stock_ledger.application.classify import resolve_type_category
from stock_ledger.application.import_analysis import (
    TypeGroup,
    apply_type_resolutions,
    completeness_issues,
    verify_fee_merge,
)
from stock_ledger.parsers.columns import COLUMN_ALIASES
from stock_ledger.parsers.huatai import parse_bill, parse_bill_with_plan
from stock_ledger.parsers.mapping import (
    MappingItem,
    MappingPlan,
    column_keys,
    derive_type_key,
    header_signature,
    merge_fee_units,
    plan_from_legacy_mapping,
    suggest_items,
    validate_plan,
)
from stock_ledger.parsers.profiles import detect_broker
from stock_ledger.parsers.table import inspect_table, read_rows

CLOCK = FrozenClock(datetime(2026, 7, 1, 9, 0, tzinfo=UTC))
_API_DIR = Path(__file__).parents[2] / "api"
_BILLS = (
    _API_DIR / "华泰账单_mock.xlsx",
    _API_DIR / "华泰账单_货基_mock.xlsx",
    _API_DIR / "华泰账单_真实节选_mock.xlsx",
)


def test_alias_catalog_covers_legacy_except_note() -> None:
    """附录 A 覆盖闸门：legacy 的每个别名（除「备注」）都能被 v2 识别到同一字段。"""
    for field_name, aliases in COLUMN_ALIASES.items():
        for alias in aliases:
            target = "note" if field_name == "summary" and alias == "备注" else field_name
            assert alias in FIELD_ALIASES[target], f"{alias} 未覆盖到 {target}"


def test_column_keys_dedupe_duplicate_headers() -> None:
    keys = column_keys(["摘要", "摘要", "备注"])
    assert len(set(keys)) == 3
    assert keys[1].startswith(f"{keys[0]}-")
    assert header_signature([" 摘要 ", "摘要"]) == header_signature(["摘要", "摘要"])


def test_validate_plan_rejects_duplicate_field_and_bad_options() -> None:
    with pytest.raises(ApiError) as exc:
        validate_plan(
            MappingPlan(
                header_row_index=0,
                items=(MappingItem("a", "amount"), MappingItem("b", "amount")),
            )
        )
    assert exc.value.code == ErrorCode.IMPORT_MAPPING_INVALID
    assert any(error.code == "duplicate_field" for error in exc.value.errors)

    with pytest.raises(ApiError) as exc2:
        validate_plan(
            MappingPlan(
                header_row_index=0,
                items=(MappingItem("a", "trade_date", {"nope": True}),),
            )
        )
    assert any(error.code == "invalid_option" for error in exc2.value.errors)


def test_validate_plan_allows_other_fee_merge_up_to_limit() -> None:
    assert "other_fee" in MERGEABLE_FIELDS
    validate_plan(
        MappingPlan(
            header_row_index=0,
            items=(MappingItem("a", "other_fee"), MappingItem("b", "other_fee")),
        )
    )
    too_many = tuple(MappingItem(f"k{idx}", "other_fee") for idx in range(9))
    with pytest.raises(ApiError) as exc:
        validate_plan(MappingPlan(header_row_index=0, items=too_many))
    assert any(error.code == "too_many_columns" for error in exc.value.errors)


def test_validate_plan_rejects_ignored_column_mapped() -> None:
    with pytest.raises(ApiError) as exc:
        validate_plan(
            MappingPlan(
                header_row_index=0,
                items=(MappingItem("a", "trade_date"),),
                ignored=("a",),
            )
        )
    assert any(error.code == "ignored_column_mapped" for error in exc.value.errors)


def test_fee_merge_is_int_domain() -> None:
    assert merge_fee_units([1, 2, 3]) == 6
    assert isinstance(merge_fee_units([1, 2]), int)


def test_derive_type_key_never_guesses() -> None:
    assert derive_type_key(-100) == "derived:buy"
    assert derive_type_key(100) == "derived:sell"
    assert derive_type_key(100) == "derived:sell"
    assert derive_type_key(0) == "derived:zero"
    assert derive_type_key(0, 500) == "derived:unknown"


def test_suggest_items_maps_huatai_headers_and_signed_amount() -> None:
    headers = [
        "成交日期",
        "证券代码",
        "证券名称",
        "买卖标志",
        "成交数量",
        "成交价格",
        "发生金额",
        "佣金",
        "印花税",
        "其他费",
        "过户费",
        "资金余额",
        "摘要",
        "备注",
    ]
    items, unresolved = suggest_items(headers)
    by_field: dict[str, list[MappingItem]] = {}
    for item in items:
        by_field.setdefault(item.field, []).append(item)
    assert unresolved == []
    assert by_field["trade_date"][0].column_key == column_keys(headers)[0]
    assert len(by_field["other_fee"]) == 2
    assert by_field["amount"][0].options == {"amount_signed": True}
    assert by_field["note"][0].column_key == column_keys(headers)[-1]


def test_detect_broker_huatai_high_and_generic_low() -> None:
    huatai = detect_broker(
        [
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
    )
    assert huatai.code == "huatai"
    assert huatai.confidence == "high"
    assert len(huatai.evidence) >= 6

    generic = detect_broker(["日期", "金额", "说明"])
    assert generic.code == "generic"
    assert generic.confidence == "low"


def test_detect_broker_ambiguous_when_two_brokers_tie() -> None:
    rules = [
        {"broker": "alpha", "header_norm": "日期", "match_type": "exact", "is_fingerprint": True},
        {"broker": "alpha", "header_norm": "金额", "match_type": "exact", "is_fingerprint": True},
        {"broker": "alpha", "header_norm": "数量", "match_type": "exact", "is_fingerprint": True},
        {"broker": "beta", "header_norm": "日期", "match_type": "exact", "is_fingerprint": True},
        {"broker": "beta", "header_norm": "金额", "match_type": "exact", "is_fingerprint": True},
        {"broker": "beta", "header_norm": "数量", "match_type": "exact", "is_fingerprint": True},
    ]
    detection = detect_broker(["日期", "金额", "数量"], rules)
    assert detection.ambiguous is True
    assert detection.code in {"alpha", "beta"}


@pytest.mark.parametrize("path", _BILLS, ids=lambda path: path.stem)
def test_plan_parser_matches_legacy_field_by_field(path: Path) -> None:
    data = path.read_bytes()
    inspection = inspect_table(data, path.name)
    plan = plan_from_legacy_mapping(inspection.headers, inspection.header_row_index, None)

    legacy = parse_bill(data, path.name, mapping=None, date_format=None, clock=CLOCK)
    planned = parse_bill_with_plan(
        data,
        path.name,
        plan=plan,
        date_format=legacy.date_format,
        clock=CLOCK,
        broker="huatai",
    )
    assert asdict(planned) == asdict(legacy)


def test_generic_profile_fails_closed_on_sxt_bill() -> None:
    path = _API_DIR / "华泰账单_真实节选_mock.xlsx"
    data = path.read_bytes()
    inspection = inspect_table(data, path.name)
    plan = plan_from_legacy_mapping(inspection.headers, inspection.header_row_index, None)
    with pytest.raises(ApiError) as exc:
        parse_bill_with_plan(
            data, path.name, plan=plan, date_format=None, clock=CLOCK, broker="generic"
        )
    assert exc.value.code == ErrorCode.IMPORT_PROFILE_REQUIRED


def test_other_fee_multi_column_is_summed_in_plan() -> None:
    workbook = Workbook()
    sheet = workbook.active
    headers = [
        "成交日期",
        "证券代码",
        "成交数量",
        "成交价格",
        "成交金额",
        "其他费",
        "过户费",
        "摘要",
    ]
    sheet.append(headers)
    sheet.append(["20260304", "600519", "100", "10.00", "1000.00", "1.00", "0.50", "证券买入"])
    buffer = io.BytesIO()
    workbook.save(buffer)
    data = buffer.getvalue()

    inspection = inspect_table(data, "bill.xlsx")
    keys = inspection.keys
    plan = MappingPlan(
        header_row_index=inspection.header_row_index,
        items=(
            MappingItem(keys[0], "trade_date"),
            MappingItem(keys[1], "security_code"),
            MappingItem(keys[2], "quantity"),
            MappingItem(keys[3], "price"),
            MappingItem(keys[4], "amount"),
            MappingItem(keys[5], "other_fee"),
            MappingItem(keys[6], "other_fee"),
            MappingItem(keys[7], "summary"),
        ),
    )
    bill = parse_bill_with_plan(data, "bill.xlsx", plan=plan, date_format=None, clock=CLOCK)
    row = bill.rows[0]
    assert row.other_fee_units == 150
    # 无买卖标志列时，毛额无法确定符号 → amount=0（方向由后续 Category 决定）。
    assert row.amount_units == 0
    assert WarningCode.MISSING_CASH_BALANCE in bill.warnings


def test_unmapped_column_is_reported_not_rejected() -> None:
    data = _BILLS[0].read_bytes()
    inspection = inspect_table(data, "bill.xlsx")
    plan = plan_from_legacy_mapping(inspection.headers, inspection.header_row_index, None)
    # 去掉「摘要」映射：解析不应失败，列进入 unresolved 供 UI 提示确认。
    plan = MappingPlan(
        header_row_index=plan.header_row_index,
        items=tuple(item for item in plan.items if item.field != "summary"),
        ignored=tuple(
            inspection.keys[idx]
            for idx, header in enumerate(inspection.headers)
            if header == "摘要"
        ),
    )
    bill = parse_bill_with_plan(
        data, "bill.xlsx", plan=plan, date_format=None, clock=CLOCK, broker="huatai"
    )
    assert "摘要" in bill.unresolved


def test_read_rows_matches_inspection_headers() -> None:
    data = _BILLS[0].read_bytes()
    rows = read_rows(data, ".xlsx")
    inspection = inspect_table(data, "bill.xlsx")
    assert rows[inspection.header_row_index] == inspection.headers


def test_trailing_blank_columns_trimmed_for_padded_sheet() -> None:
    """Excel dimension 被撑到数据列之外时，不应把尾部空列带进表头与预览。"""
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["成交日期", "证券代码", "成交数量", "成交价格", "成交金额", "摘要"])
    sheet.append(["20260304", "600519", "100", "10.00", "1000.00", "证券买入"])
    # 只有格式没有内容的单元格会把工作表 dimension 撑到 Z 列。
    sheet["Z1"] = " "
    buffer = io.BytesIO()
    workbook.save(buffer)
    data = buffer.getvalue()

    rows = read_rows(data, ".xlsx")
    assert {len(row) for row in rows} == {6}

    inspection = inspect_table(data, "bill.xlsx")
    assert inspection.headers == [
        "成交日期",
        "证券代码",
        "成交数量",
        "成交价格",
        "成交金额",
        "摘要",
    ]
    assert inspection.unresolved_headers == []


def test_trailing_blank_columns_trimmed_for_csv() -> None:
    """CSV 尾部连续空单元格同样裁掉；只有表头的空列仍保留。"""
    data = (
        "成交日期,证券代码,成交数量,成交价格,成交金额,摘要,备注,\n"
        "20260304,600519,100,10.00,1000.00,证券买入,,\n"
    ).encode()
    inspection = inspect_table(data, "bill.csv")
    assert inspection.headers == [
        "成交日期",
        "证券代码",
        "成交数量",
        "成交价格",
        "成交金额",
        "摘要",
        "备注",
    ]


_TEXT_XLS_HEADERS = (
    "发生日期",
    "业务名称",
    "证券代码",
    "证券名称",
    "成交均价",
    "成交数量",
    "成交金额",
    "股份余额",
    "手续费",
    "印花税",
    "过户费",
    "其他费",
    "发生金额",
    "资金余额",
    "委托编号",
    "成交编号",
    "委托价格",
    "委托数量",
    "股东代码",
    "资金帐号",
    "币种",
    "备注",
)
_TEXT_XLS_QUOTED = frozenset(
    {
        "发生日期",
        "业务名称",
        "证券代码",
        "证券名称",
        "委托编号",
        "成交编号",
        "股东代码",
        "资金帐号",
        "币种",
        "备注",
    }
)


def _text_xls_bill() -> bytes:
    base = dict.fromkeys(_TEXT_XLS_HEADERS, "")
    rows = [
        {
            **base,
            "发生日期": "2020-01-02",
            "业务名称": "证券买入",
            "证券代码": "000123",
            "证券名称": "测试股票",
            "成交均价": "7.2700",
            "成交数量": "400",
            "成交金额": "2908.00",
            "股份余额": "400",
            "手续费": "5.00",
            "印花税": "0.00",
            "过户费": "0.00",
            "其他费": "0.000",
            "发生金额": "-2913.00",
            "资金余额": "87.19",
            "委托编号": "TESTORDER",
            "成交编号": "TESTDEAL",
            "委托价格": "7.2700",
            "委托数量": "400",
            "股东代码": "TESTSHAREHOLDER",
            "资金帐号": "TESTACCOUNT",
            "币种": "人民币",
        },
        {
            **base,
            "发生日期": "2020-01-03",
            "业务名称": "银行转存",
            "成交均价": "0.0000",
            "成交数量": "0",
            "成交金额": "0.00",
            "股份余额": "0",
            "手续费": "0.00",
            "印花税": "0.00",
            "过户费": "0.00",
            "其他费": "0.000",
            "发生金额": "3000.00",
            "资金余额": "3087.19",
            "委托价格": "0.0000",
            "委托数量": "0",
            "资金帐号": "TESTACCOUNT",
            "币种": "人民币",
        },
    ]

    def cell(header: str, value: str) -> str:
        if value and header in _TEXT_XLS_QUOTED:
            return f'="{value}"'
        return value

    matrix = [[f'="{header}"' for header in _TEXT_XLS_HEADERS]]
    matrix.extend([[cell(header, row[header]) for header in _TEXT_XLS_HEADERS] for row in rows])
    text = "".join("\t".join(row) + "\t\r\n" for row in matrix)
    return text.encode("gbk")


def test_text_xls_reads_gbk_tab_cells_without_xlrd(monkeypatch: pytest.MonkeyPatch) -> None:
    def reject_xlrd(*, file_contents: bytes) -> None:
        raise AssertionError("文本型 XLS 不应调用 xlrd")

    monkeypatch.setattr("stock_ledger.parsers.table.xlrd.open_workbook", reject_xlrd)
    data = _text_xls_bill()
    inspection = inspect_table(data, "statement.xls")
    assert inspection.headers == list(_TEXT_XLS_HEADERS)
    assert inspection.row_count == 2
    assert inspection.rows[0][2] == "000123"

    items, unresolved = suggest_items(inspection.headers)
    keys = dict(zip(inspection.headers, inspection.keys, strict=True))
    # 同一账单同时有毛额「成交金额」与净额「发生金额」时，建议必须落在「发生金额」，
    # 否则非交易资金行（银证转入/利息归本）的毛额恒为 0，现金流被静默丢弃。
    by_field = {item.field: item for item in items}
    assert by_field["amount"].column_key == keys["发生金额"]
    assert by_field["amount"].options == {"amount_signed": True}
    assert keys["成交金额"] in unresolved
    plan = MappingPlan(
        header_row_index=inspection.header_row_index,
        items=tuple(items),
        ignored=tuple(unresolved),
    )
    bill = parse_bill_with_plan(
        data, "statement.xls", plan=plan, date_format=None, clock=CLOCK, broker="generic"
    )
    assert [row.amount_units for row in bill.rows] == [-291300, 300000]
    assert bill.rows[0].security_code == "000123"


def test_suggest_items_prefers_signed_amount_column() -> None:
    """真实华泰文本 XLS 表头：发生金额优先于成交金额，其余列建议不变。"""
    headers = list(_TEXT_XLS_HEADERS)
    keys = column_keys(headers)
    items, unresolved = suggest_items(headers)
    by_key = {item.column_key: item for item in items}
    got = {
        header: (by_key[key].field if key in by_key else None)
        for header, key in zip(headers, keys, strict=True)
    }
    assert got == {
        "发生日期": "trade_date",
        "业务名称": "summary",
        "证券代码": "security_code",
        "证券名称": "security_name",
        "成交均价": "price",
        "成交数量": "quantity",
        "成交金额": None,
        "股份余额": None,
        "手续费": "commission",
        "印花税": "stamp_tax",
        "过户费": "other_fee",
        "其他费": "other_fee",
        "发生金额": "amount",
        "资金余额": "cash_balance",
        "委托编号": None,
        "成交编号": None,
        "委托价格": None,
        "委托数量": None,
        "股东代码": None,
        "资金帐号": "account",
        "币种": None,
        "备注": "note",
    }
    assert by_key[keys[headers.index("发生金额")]].options == {"amount_signed": True}
    assert keys[headers.index("成交金额")] in unresolved
    # 股东代码是沪/深股东号，不再被当成资金账号。
    assert keys[headers.index("股东代码")] not in by_key


def test_text_xls_legacy_and_plan_parser_match() -> None:
    data = _text_xls_bill()
    inspection = inspect_table(data, "statement.xls")
    plan = plan_from_legacy_mapping(inspection.headers, inspection.header_row_index, None)
    legacy = parse_bill(data, "statement.xls", mapping=None, date_format=None, clock=CLOCK)
    planned = parse_bill_with_plan(
        data, "statement.xls", plan=plan, date_format=None, clock=CLOCK, broker="huatai"
    )
    assert asdict(planned) == asdict(legacy)


def test_non_text_xls_still_uses_xlrd(monkeypatch: pytest.MonkeyPatch) -> None:
    called = False

    def open_workbook(*, file_contents: bytes) -> None:
        nonlocal called
        called = True
        raise RuntimeError("xlrd sentinel")

    monkeypatch.setattr("stock_ledger.parsers.table.xlrd.open_workbook", open_workbook)
    with pytest.raises(RuntimeError, match="xlrd sentinel"):
        read_rows(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1", ".xls")
    assert called is True


def _plan(items: list[tuple[str, str]]) -> MappingPlan:
    return MappingPlan(
        header_row_index=0,
        items=tuple(MappingItem(key, field_name) for key, field_name in items),
    )


def test_completeness_requires_l1_and_complete_trade_fields() -> None:
    full = [
        ("date", "trade_date"),
        ("code", "security_code"),
        ("qty", "quantity"),
        ("price", "price"),
        ("amount", "amount"),
        ("summary", "summary"),
    ]
    assert completeness_issues(_plan(full)) == []
    for field_name in ("trade_date", "security_code", "quantity", "price"):
        issues = completeness_issues(_plan([item for item in full if item[1] != field_name]))
        assert field_name in {issue["field"] for issue in issues}


def test_completeness_allows_only_fully_mapped_cash_mode() -> None:
    assert (
        completeness_issues(
            _plan([("date", "trade_date"), ("amount", "amount"), ("summary", "summary")])
        )
        == []
    )
    issues = completeness_issues(
        _plan([("date", "trade_date"), ("amount", "amount"), ("side", "side")])
    )
    assert "summary" in {issue["field"] for issue in issues}


@pytest.mark.parametrize(
    ("resolutions", "expected"),
    [
        ([], ErrorCode.IMPORT_BILL_TYPES_UNRESOLVED),
        (
            [{"type_key": "不存在", "category": "other"}],
            ErrorCode.IMPORT_MAPPING_INVALID,
        ),
        (
            [
                {"type_key": "证券买入", "category": "buy"},
                {"type_key": "证券买入", "category": "buy"},
            ],
            ErrorCode.IMPORT_MAPPING_INVALID,
        ),
        (
            [{"type_key": "证券买入", "category": "invalid"}],
            ErrorCode.IMPORT_CATEGORY_INVALID,
        ),
        (
            [{"type_key": "证券买入", "category": "buy", "discard": True}],
            ErrorCode.IMPORT_CATEGORY_INVALID,
        ),
    ],
)
def test_type_resolution_validation(resolutions, expected: ErrorCode) -> None:
    groups = {"证券买入": TypeGroup(type_key="证券买入", type_label="证券买入")}
    with pytest.raises(ApiError) as exc:
        apply_type_resolutions(groups, resolutions)
    assert exc.value.code == expected


def test_resolve_type_category_prefers_memory_then_rule() -> None:
    rule = SimpleNamespace(
        id="rul_test",
        code="sys_test",
        category="transfer",
        side="both",
        match_type="contains",
        pattern="证券",
        priority=1,
    )
    known = SimpleNamespace(category="dividend", discarded=False)
    assert resolve_type_category(
        " 证券 买入 ", known_types={"证券买入": known}, rules=[rule], summary="证券买入"
    ) == ("dividend", "sys_test", False)

    discarded = SimpleNamespace(category=None, discarded=True)
    assert resolve_type_category(
        "证券买入",
        known_types={"证券买入": discarded},
        rules=[rule],
        summary="证券买入",
    ) == (None, None, True)
    assert resolve_type_category("证券买入", known_types={}, rules=[rule], summary="证券买入") == (
        "transfer",
        "sys_test",
        False,
    )
    assert resolve_type_category("其他", known_types={}, rules=[rule], summary="其他") == (
        None,
        None,
        False,
    )


def _two_fee_csv(*, fee_a: str = "1.00", second_balance: str = "10098.00") -> bytes:
    return (
        "成交日期,证券代码,证券名称,买卖标志,成交数量,成交价格,成交金额,"
        "佣金,印花税,费用甲,费用乙,资金余额,摘要\n"
        f"2026-04-01,600001,测试,买入,100,10.00,1000.00,0,0,{fee_a},0.50,8998.50,证券买入\n"
        f"2026-04-02,600001,测试,卖出,100,11.00,1100.00,0,0,0.25,0.25,{second_balance},证券卖出\n"
    ).encode()


def _parse_two_fee(data: bytes):
    inspection = inspect_table(data, "bill.csv")
    items, _unresolved = suggest_items(inspection.headers)
    keys = dict(zip(inspection.headers, inspection.keys, strict=True))
    items.extend(
        [MappingItem(keys["费用甲"], "other_fee"), MappingItem(keys["费用乙"], "other_fee")]
    )
    plan = MappingPlan(header_row_index=inspection.header_row_index, items=tuple(items))
    bill = parse_bill_with_plan(data, "bill.csv", plan=plan, date_format=None, clock=CLOCK)
    return inspection, plan, bill


@pytest.mark.parametrize("value", ["", "-", "--", "+", "—"])
def test_fee_merge_blank_symbols_are_zero(value: str) -> None:
    inspection, plan, bill = _parse_two_fee(_two_fee_csv(fee_a=value))
    verification, warnings = verify_fee_merge(inspection, plan, bill)
    assert verification["fee_merge"]["status"] == "ok"
    assert warnings == [WarningCode.FEE_MERGE_APPLIED.value]


@pytest.mark.parametrize("value", ["abc", "-1.00", "1000000000001.00"])
def test_fee_merge_rejects_invalid_negative_and_overflow(value: str) -> None:
    inspection, plan, bill = _parse_two_fee(_two_fee_csv(fee_a=value))
    with pytest.raises(ApiError) as exc:
        verify_fee_merge(inspection, plan, bill)
    assert exc.value.code == ErrorCode.IMPORT_FEE_MERGE_INVALID
    assert any("第 2 行" in error.message for error in exc.value.errors)


def test_fee_merge_balance_chain_success_and_mismatch_line() -> None:
    inspection, plan, bill = _parse_two_fee(_two_fee_csv())
    verification, _warnings = verify_fee_merge(inspection, plan, bill)
    assert verification["fee_merge"]["checked_rows"] == 1
    assert [row.other_fee_units for row in bill.rows] == [150, 50]
    assert [row.amount_units for row in bill.rows] == [-100150, 109950]

    inspection, plan, bill = _parse_two_fee(_two_fee_csv(second_balance="10000.00"))
    with pytest.raises(ApiError) as exc:
        verify_fee_merge(inspection, plan, bill)
    assert exc.value.code == ErrorCode.IMPORT_FEE_MERGE_VERIFY_FAILED
    assert any("第 3 行" in error.message for error in exc.value.errors)
