"""华泰证券对账单解析器（纯函数、无 I/O 直读）。

输入 bytes + file_name，输出结构化 `ParsedBill`。之所以做成纯函数：解析结果要能被
预览（不落库）与提交（落库）两个阶段复用，并可被属性测试反复回放。
所有金额/数量/价格都经 `Decimal(str) → 标度整数`，绝不出现 float。

金额口径（全仓统一，对账直接使用）：`ParsedRow.amount_units` 一律是**带符号的发生金额
（净现金流，已含费用）**——买入为负、卖出为正；非交易资金业务按资金方向（转入为卖出方向
正、转出为买入方向负）；方向不可判定且没有金额流时为 0。账单里原生的「发生金额/发生额」
列已满足该口径，直接采用；「成交金额」毛额（及按量×价推导出的毛额）则按方向换算：
买入 `-(毛额+费用)`、卖出 `毛额-费用`，费用 = 佣金 + 印花税 + 其他费。
"""

from __future__ import annotations

import csv
import hashlib
import io
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import xlrd  # type: ignore[import-untyped]  # 第三方 xlrd 无类型桩（types-xlrd 未安装）
from openpyxl import load_workbook  # type: ignore[import-untyped]  # 第三方 openpyxl 无类型桩
from stock_contracts.clock import Clock
from stock_contracts.constants import FILE_MAX_BYTES
from stock_contracts.enums import QuarantineReason, Side, WarningCode
from stock_contracts.errors import ApiError, ErrorCode, FieldError
from stock_core.values import Money, Price, Qty

from stock_ledger.parsers.columns import (
    ColumnMapping,
    detect_date_format,
    is_non_trade_summary,
    is_signed_amount_header,
    map_columns,
    normalize_cell,
    parse_date_text,
    resolve_side,
)
from stock_ledger.parsers.dedupe import core_signature, dedupe_key

SUPPORTED_SUFFIXES: frozenset[str] = frozenset({".xlsx", ".xls", ".csv"})
_HEADER_SCAN_ROWS = 30
_CURRENCY_NOISE = ("¥", "￥", "元", " ", ",", "\u3000")


@dataclass(slots=True)
class QuarantineCandidate:
    """无法进入明细的行：保留原始内容 + 原因码，供隔离区回填。"""

    line_no: int
    raw: dict[str, str]
    reason_code: str
    suggested_rule_id: str | None = None


@dataclass(slots=True)
class ParsedRow:
    """一条可入明细的交易（金额/数量/价格均为标度整数）。"""

    line_no: int
    trade_date: date
    security_code: str
    security_name: str
    side: str
    quantity_units: int
    price_units: int
    # 带符号发生金额（净现金流，含费用）：买入为负、卖出为正；对账直接用 prev+amount。
    amount_units: int
    commission_units: int
    stamp_tax_units: int
    other_fee_units: int
    cash_balance_units: int | None
    cash_balance_available: bool
    summary: str | None
    summary_code: str | None
    account_masked: str | None
    occurrence_idx: int
    dedupe_key: str
    raw: dict[str, str]


@dataclass(slots=True)
class ParsedBill:
    """一次解析的完整结果（预览与提交共用）。"""

    rows: list[ParsedRow] = field(default_factory=list)
    columns: dict[str, str] = field(default_factory=dict)
    unresolved: list[str] = field(default_factory=list)
    date_format: str | None = None
    warnings: list[WarningCode] = field(default_factory=list)
    quarantined: list[QuarantineCandidate] = field(default_factory=list)
    assumptions: dict[str, Any] = field(default_factory=dict)

    @property
    def row_count(self) -> int:
        return len(self.rows) + len(self.quarantined)


class _RowError(Exception):
    """单行解析失败（内部信号，外部统一转隔离）。"""

    __slots__ = ("reason",)

    def __init__(self, reason: QuarantineReason) -> None:
        super().__init__(reason.value)
        self.reason = reason


@dataclass(slots=True)
class _Candidate:
    parsed: ParsedRow
    raw_values: tuple[str, ...]


def _validate_file(data: bytes, file_name: str) -> str:
    suffix = Path(file_name).suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        raise ApiError(
            ErrorCode.IMPORT_UNSUPPORTED_FILE,
            detail=f"不支持的扩展名: {suffix or file_name!r}",
        )
    if len(data) > FILE_MAX_BYTES:
        raise ApiError(ErrorCode.IMPORT_FILE_TOO_LARGE, detail="文件超过 20MB")
    return suffix


def _decode_csv(data: bytes) -> str:
    for encoding in ("utf-8-sig", "gbk"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _read_rows(data: bytes, suffix: str) -> list[list[str]]:
    """把三种文件格式统一读成字符串二维表（数值单元格经 str，不引入 float）。"""
    if suffix == ".xlsx":
        workbook = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        try:
            sheet = workbook.worksheets[0]
            return [
                [normalize_cell(cell) for cell in row] for row in sheet.iter_rows(values_only=True)
            ]
        finally:
            workbook.close()
    if suffix == ".xls":
        book = xlrd.open_workbook(file_contents=data)
        sheet = book.sheet_by_index(0)
        return [
            [normalize_cell(sheet.cell_value(r, c)) for c in range(sheet.ncols)]
            for r in range(sheet.nrows)
        ]
    reader = csv.reader(io.StringIO(_decode_csv(data)))
    return [[normalize_cell(cell) for cell in row] for row in reader]


def _locate_header(
    rows: list[list[str]], mapping: dict[str, str] | None
) -> tuple[int, list[str], list[list[str]]]:
    """在前若干行里挑“识别到最多列”的一行作表头（账单常有抬头/说明行）。"""
    best_idx = 0
    best_count = 0
    best_headers: list[str] = rows[0] if rows else []
    for idx in range(min(len(rows), _HEADER_SCAN_ROWS)):
        found: ColumnMapping = map_columns(rows[idx], mapping=mapping)
        count = len(found.columns)
        if count > best_count:
            best_idx, best_count, best_headers = idx, count, rows[idx]
    return best_idx, best_headers, rows[best_idx + 1 :]


def _decimal(raw: str) -> Decimal:
    """单元格文本 → Decimal（去千分位/货币符号）；空值按 0。"""
    cleaned = raw
    for noise in _CURRENCY_NOISE:
        cleaned = cleaned.replace(noise, "")
    if cleaned in ("", "-", "+"):
        return Decimal(0)
    try:
        return Decimal(cleaned)
    except InvalidOperation as exc:
        raise _RowError(QuarantineReason.INVALID_ROW) from exc


def _units(raw: str, kind: type[Money] | type[Price] | type[Qty]) -> int:
    return kind.from_decimal(_decimal(raw)).units


def _gross_to_net_units(gross_units: int, side: str, fees_units: int) -> int:
    """毛成交额 → 带符号净现金流（分）：买入 `-(毛额+费用)`，卖出 `毛额-费用`。"""
    if side == Side.SELL.value:
        return gross_units - fees_units
    return -(gross_units + fees_units)


def _net_from_gross_units(gross_units: int, side: str, fees_units: int) -> tuple[int, bool]:
    """毛额 → 净现金流；方向未知时无法定号（置 0 并回报），否则按买卖方向换算。"""
    if side == Side.UNKNOWN.value:
        return 0, gross_units != 0
    return _gross_to_net_units(gross_units, side, fees_units), False


def _net_amount_units(
    raw_text: str, *, signed: bool, side: str, fees_units: int
) -> tuple[int, bool]:
    """金额列原文 → 带符号净现金流；返回 `(units, 是否因方向未知而无法定号)`。

    - 「发生金额/发生额」原生带符号 → 直接采用（费用已含在列内，绝不重复扣减）；
    - 毛额列里出现负数 → 只可能是已带符号的净额（毛额不可能为负），按净额采用；
    - 其余按方向换算；方向 unknown 时资金流向不可判定，置 0 并回报调用方留痕。
    """
    raw_units = _units(raw_text, Money)
    if signed or raw_units < 0:
        return raw_units, False
    return _net_from_gross_units(raw_units, side, fees_units)


def _build_candidate(
    raw: dict[str, str],
    field_headers: dict[str, str],
    date_format_code: str,
    line_no: int,
    *,
    amount_signed: bool,
) -> tuple[ParsedRow, bool, bool]:
    """单行 → ParsedRow；失败抛 _RowError 由调用方隔离。

    返回 `(行, 金额是否按量×价派生, 金额是否因方向未知而无法定号)`。
    """

    def value(name: str) -> str:
        header = field_headers.get(name)
        return raw.get(header, "") if header is not None else ""

    code = value("security_code")
    if not code:
        # 非交易类业务（银行转存/转取、股息入帐等）证券代码可空，用 CASH 占位导入。
        if is_non_trade_summary(value("summary")):
            code = "CASH"
        else:
            raise _RowError(QuarantineReason.INVALID_ROW)
    # 方向：买卖标志列可缺失/为空，此时回退摘要识别，仍无则 unknown（不隔离该行）。
    side = resolve_side(value("side"), value("summary"))
    try:
        year, month, day = parse_date_text(value("trade_date"), date_format_code)
        trade_date = date(year, month, day)
    except ValueError as exc:
        raise _RowError(QuarantineReason.INVALID_ROW) from exc

    quantity_units = _units(value("quantity"), Qty)
    price_units = _units(value("price"), Price)

    commission_units = _units(value("commission"), Money)
    stamp_tax_units = _units(value("stamp_tax"), Money)
    other_fee_units = _units(value("other_fee"), Money)
    fees_units = commission_units + stamp_tax_units + other_fee_units

    amount_text = value("amount")
    if amount_text:
        amount_units, amount_unsigned = _net_amount_units(
            amount_text, signed=amount_signed, side=side, fees_units=fees_units
        )
        amount_derived = False
    else:
        # 无金额列：先按量×价推导毛额，再按方向换算成净现金流（不能用原始毛额入库）。
        gross_units = Money.from_decimal(
            Qty(quantity_units).to_decimal() * Price(price_units).to_decimal()
        ).units
        amount_units, amount_unsigned = _net_from_gross_units(gross_units, side, fees_units)
        amount_derived = True

    balance_text = value("cash_balance")
    cash_balance_units: int | None = None
    if balance_text:
        cash_balance_units = _units(balance_text, Money)

    summary = value("summary") or None
    # 账号明文已在 parse_bill 入口统一抹成 MD5（空账号留空串 → 这里转 None）。
    # 此值同时作为 ParsedRow.account_masked（落库列）与下游去重键的业务要素。
    account_masked = value("account") or None
    # 非交易行证券名称为空时，用摘要填充（如「银行转存」），避免默认成 CASH 无法辨识业务。
    security_name = value("security_name") or (summary or "资金业务" if code == "CASH" else code)

    row = ParsedRow(
        line_no=line_no,
        trade_date=trade_date,
        security_code=code,
        security_name=security_name,
        side=side,
        quantity_units=quantity_units,
        price_units=price_units,
        amount_units=amount_units,
        commission_units=commission_units,
        stamp_tax_units=stamp_tax_units,
        other_fee_units=other_fee_units,
        cash_balance_units=cash_balance_units,
        cash_balance_available=cash_balance_units is not None,
        summary=summary,
        summary_code=None,
        account_masked=account_masked,
        occurrence_idx=0,
        dedupe_key="",
        raw=raw,
    )
    return row, amount_derived, amount_unsigned


def parse_bill(
    data: bytes,
    file_name: str,
    *,
    mapping: dict[str, str] | None,
    date_format: str | None,
    clock: Clock,
) -> ParsedBill:
    """解析华泰账单；所有业务失败以 ApiError/隔离行表达，不抛裸异常。"""
    suffix = _validate_file(data, file_name)
    raw_rows = _read_rows(data, suffix)
    header_idx, headers, data_rows = _locate_header(raw_rows, mapping)

    found = map_columns(headers, mapping=mapping)
    missing_required = found.missing_required()
    if missing_required:
        raise ApiError(
            ErrorCode.IMPORT_COLUMNS_UNRESOLVED,
            errors=[
                FieldError("columns", "missing_required_column", f"缺少必需列: {name}")
                for name in missing_required
            ],
            detail="账单缺少必需列",
        )
    field_headers = found.field_headers()
    # 金额列语义由原始表头决定：「发生金额/发生额」带符号直接用，其余按毛额+方向换算。
    amount_signed = is_signed_amount_header(field_headers.get("amount"))

    warnings: list[WarningCode] = []
    assumptions: dict[str, Any] = {}

    trade_date_header = field_headers.get("trade_date")
    samples: list[str] = []
    if trade_date_header is not None:
        for row in data_rows:
            idx = headers.index(trade_date_header)
            if idx < len(row) and row[idx]:
                samples.append(row[idx])
            if len(samples) >= 20:
                break

    detected_format, detected_warnings = detect_date_format(samples)
    if date_format is not None:
        effective_format = date_format
    else:
        effective_format = detected_format or "YYYY-MM-DD"
        warnings.extend(detected_warnings)
        if (
            detected_format is None and samples
        ) or WarningCode.DATE_FORMAT_AMBIGUOUS in detected_warnings:
            assumptions["date_format"] = effective_format

    candidates: list[_Candidate] = []
    quarantined: list[QuarantineCandidate] = []
    derived_count = 0
    unsigned_amount_count = 0

    for offset, row in enumerate(data_rows):
        line_no = header_idx + offset + 2
        if not any(cell for cell in row):
            continue
        raw = {header: (row[i] if i < len(row) else "") for i, header in enumerate(headers)}
        # 把账号列明文替换成 MD5 摘要：隔离区 raw 和预览 columns_detail.sample
        # 都从这里拿原始行字典，源头抹一次即可，下游都安全。
        account_header = field_headers.get("account")
        if account_header is not None:
            account_text = raw.get(account_header, "")
            raw[account_header] = (
                hashlib.md5(account_text.encode("utf-8")).hexdigest() if account_text else ""
            )
        try:
            parsed, amount_derived, amount_unsigned = _build_candidate(
                raw,
                field_headers,
                effective_format,
                line_no,
                amount_signed=amount_signed,
            )
        except _RowError as err:
            quarantined.append(
                QuarantineCandidate(line_no=line_no, raw=raw, reason_code=err.reason.value)
            )
            continue
        if amount_derived:
            derived_count += 1
        if amount_unsigned:
            unsigned_amount_count += 1
        candidates.append(
            _Candidate(parsed=parsed, raw_values=tuple(raw.get(h, "") for h in headers))
        )

    kept: list[_Candidate] = []
    seen_full: set[tuple[str, ...]] = set()
    for cand in candidates:
        if cand.raw_values in seen_full:
            quarantined.append(
                QuarantineCandidate(
                    line_no=cand.parsed.line_no,
                    raw=cand.parsed.raw,
                    reason_code=QuarantineReason.DUPLICATE_IN_FILE.value,
                )
            )
            continue
        seen_full.add(cand.raw_values)
        kept.append(cand)

    counts: dict[str, int] = {}
    rows: list[ParsedRow] = []
    for cand in kept:
        parsed_row = cand.parsed
        signature = core_signature(parsed_row)
        parsed_row.occurrence_idx = counts.get(signature, 0)
        counts[signature] = parsed_row.occurrence_idx + 1
        parsed_row.dedupe_key = dedupe_key(parsed_row)
        rows.append(parsed_row)

    if "cash_balance" not in field_headers:
        warnings.append(WarningCode.MISSING_CASH_BALANCE)
    if rows and rows[0].side != "buy":
        warnings.append(WarningCode.FIRST_ROW_NOT_BUY)
    if any(q.reason_code == QuarantineReason.DUPLICATE_IN_FILE.value for q in quarantined):
        warnings.append(WarningCode.DUPLICATE_ROWS)
    if derived_count:
        assumptions["amount_derived_rows"] = derived_count
    if unsigned_amount_count:
        # 毛额列 + 方向 unknown：资金流向不可判定，金额保守置 0，不伪造正负号。
        assumptions["amount_unsigned_rows"] = unsigned_amount_count

    return ParsedBill(
        rows=rows,
        columns=found.columns,
        unresolved=found.unresolved,
        date_format=effective_format,
        warnings=warnings,
        quarantined=quarantined,
        assumptions=assumptions,
    )


__all__ = [
    "SUPPORTED_SUFFIXES",
    "ParsedBill",
    "ParsedRow",
    "QuarantineCandidate",
    "parse_bill",
]
