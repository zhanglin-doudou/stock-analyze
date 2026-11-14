"""表格读取与表头定位（v2 导入，纯计算 + 文件解码）。

与 legacy `huatai._read_rows` 共用同一套解码/归一逻辑，但表头行定位改为
「v2 字段别名 + 管理员列规则」打分（设计 §3.2）：账单常有抬头/说明行，
取命中列最多的那一行；同一分数取最靠前的行。扫描范围与 legacy 一致（前 30 行）。
"""

from __future__ import annotations

import csv
import io
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

import xlrd  # type: ignore[import-untyped]
from openpyxl import load_workbook  # type: ignore[import-untyped]
from stock_contracts.constants import FILE_MAX_BYTES
from stock_contracts.errors import ApiError, ErrorCode
from stock_contracts.import_fields import FIELD_ALIASES

from stock_ledger.parsers.columns import normalize_cell, normalize_header
from stock_ledger.parsers.mapping import column_keys, header_signature, normalize_header_v2

SUPPORTED_SUFFIXES: frozenset[str] = frozenset({".xlsx", ".xls", ".csv"})
HEADER_SCAN_ROWS = 30
SAMPLE_ROWS = 3


class ColumnRuleLike(Protocol):
    """管理员列规则的最小形状（避免 table 反向依赖仓储层）。"""

    @property
    def header_norm(self) -> str: ...

    @property
    def match_type(self) -> str: ...

    @property
    def priority(self) -> int: ...

    @property
    def enabled(self) -> bool: ...


def validate_file(data: bytes, file_name: str) -> str:
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


def _normalize_text_xls_cell(raw: str) -> str:
    """解除券商文本导出中的完整 `=\"...\"` 包装，不执行任何公式。"""
    value = normalize_cell(raw)
    if len(value) >= 3 and value.startswith('="') and value.endswith('"'):
        return value[2:-1]
    return value


def _read_text_xls(data: bytes) -> list[list[str]] | None:
    """读取 GBK/UTF-8 的 TAB 文本型 XLS；非明确文本格式返回 None，继续走 xlrd。"""
    for encoding in ("utf-8-sig", "gbk"):
        try:
            text = data.decode(encoding)
        except UnicodeDecodeError:
            continue
        first_line = text.split("\n", 1)[0]
        if "\x00" in text or "\t" not in first_line:
            return None
        reader = csv.reader(io.StringIO(text), delimiter="\t")
        rows = [[_normalize_text_xls_cell(cell) for cell in row] for row in reader]
        width = max((len(row) for row in rows), default=0)
        while width and all(len(row) < width or not row[width - 1] for row in rows):
            width -= 1
        return [row[:width] for row in rows]
    return None


def read_rows(data: bytes, suffix: str | None = None, file_name: str = "bill") -> list[list[str]]:
    """把三种文件格式统一读成字符串二维表（数值单元格经 str，不引入 float）。"""
    effective = suffix or validate_file(data, file_name)
    if effective == ".xlsx":
        workbook = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        try:
            sheet = workbook.worksheets[0]
            return [
                [normalize_cell(cell) for cell in row] for row in sheet.iter_rows(values_only=True)
            ]
        finally:
            workbook.close()
    if effective == ".xls":
        text_rows = _read_text_xls(data)
        if text_rows is not None:
            return text_rows
        book = xlrd.open_workbook(file_contents=data)
        sheet = book.sheet_by_index(0)
        return [
            [normalize_cell(sheet.cell_value(r, c)) for c in range(sheet.ncols)]
            for r in range(sheet.nrows)
        ]
    reader = csv.reader(io.StringIO(_decode_csv(data)))
    return [[normalize_cell(cell) for cell in row] for row in reader]


def _rule_attr(rule: object, name: str, default: object = None) -> object:
    if isinstance(rule, dict):
        return rule.get(name, default)
    return getattr(rule, name, default)


def rule_matches(header_norm: str, rule: ColumnRuleLike) -> bool:
    target = normalize_header_v2(str(_rule_attr(rule, "header_norm", "")))
    if not target:
        return False
    match_type = _rule_attr(rule, "match_type", "exact")
    if match_type == "prefix":
        return header_norm.startswith(target)
    if match_type == "contains":
        return target in header_norm
    return header_norm == target


def _alias_match_count(header_norm: str) -> int:
    for aliases in FIELD_ALIASES.values():
        for alias in aliases:
            if header_norm == normalize_header_v2(alias):
                return 1
    return 0


def score_headers(headers: list[str], rules: Sequence[ColumnRuleLike] = ()) -> int:
    enabled = sorted(
        (rule for rule in rules if _rule_attr(rule, "enabled", True)),
        key=lambda rule: int(str(_rule_attr(rule, "priority", 100) or 100)),
    )
    score = 0
    for header in headers:
        norm = normalize_header_v2(header)
        if not norm:
            continue
        score += _alias_match_count(norm)
        for rule in enabled:
            if rule_matches(norm, rule):
                score += 1
                break
    return score


_DATE_CELL = re.compile(r"^\d{4}(?:[-/.]?\d{2}){2}(?:[ T].*)?$")
_NUMBER_CELL = re.compile(r"^[-+]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?$")


def _looks_like_data_row(cells: list[str]) -> bool:
    """只识别明确的「日期 + 数值」数据行，避免误伤零命中的自定义文字表头。"""
    non_empty = [cell.strip() for cell in cells if cell.strip()]
    return (
        len(non_empty) >= 2
        and any(_DATE_CELL.fullmatch(cell) for cell in non_empty)
        and any(_NUMBER_CELL.fullmatch(cell) for cell in non_empty)
    )


@dataclass(slots=True)
class HeaderCandidate:
    index: int
    headers: list[str]
    score: int
    signature: str


@dataclass(slots=True)
class TableInspection:
    """表头定位结果：预览/解析共用，不写库。"""

    headers: list[str] = field(default_factory=list)
    keys: list[str] = field(default_factory=list)
    header_row_index: int = 0
    signature: str = ""
    score: int = 0
    rows: list[list[str]] = field(default_factory=list)
    row_count: int = 0
    samples: dict[str, list[str]] = field(default_factory=dict)
    candidates: list[HeaderCandidate] = field(default_factory=list)
    unresolved_headers: list[str] = field(default_factory=list)


def _collect_samples(keys: list[str], rows: list[list[str]]) -> dict[str, list[str]]:
    samples: dict[str, list[str]] = {key: [] for key in keys}
    for row in rows:
        for idx, key in enumerate(keys):
            if len(samples[key]) >= SAMPLE_ROWS:
                continue
            value = row[idx] if idx < len(row) else ""
            if value:
                samples[key].append(value)
        if all(len(values) >= SAMPLE_ROWS for values in samples.values()):
            break
    return samples


def inspect_table(
    data: bytes,
    file_name: str,
    *,
    rules: list[ColumnRuleLike] | None = None,
) -> TableInspection:
    """读取文件并定位表头行；`rules` 为管理员维护的列规则（只影响建议与打分）。"""
    suffix = validate_file(data, file_name)
    raw_rows = read_rows(data, suffix)
    scan_limit = min(len(raw_rows), HEADER_SCAN_ROWS)
    candidates: list[HeaderCandidate] = []
    for idx in range(scan_limit):
        headers = [normalize_header(cell) for cell in raw_rows[idx]]
        score = score_headers(headers, rules or ())
        candidates.append(
            HeaderCandidate(
                index=idx,
                headers=headers,
                score=score,
                signature=header_signature(headers),
            )
        )
    best = candidates[0] if candidates else HeaderCandidate(0, [], 0, "")
    for candidate in candidates:
        if candidate.score > best.score:
            best = candidate
    if best.score == 0 and _looks_like_data_row(best.headers):
        raise ApiError(
            ErrorCode.IMPORT_LAYOUT_UNSUPPORTED,
            detail="未找到表头行；请导出为带单行表头的 CSV 后重试",
        )
    data_rows = raw_rows[best.index + 1 :]
    keys = column_keys(best.headers)
    non_empty = [row for row in data_rows if any(cell for cell in row)]
    return TableInspection(
        headers=best.headers,
        keys=keys,
        header_row_index=best.index,
        signature=best.signature,
        score=best.score,
        rows=data_rows,
        row_count=len(non_empty),
        samples=_collect_samples(keys, non_empty),
        candidates=candidates,
        unresolved_headers=[header for header in best.headers if not header],
    )


def locate_by_signature(raw_rows: list[list[str]], signature: str) -> tuple[int, list[str]] | None:
    """按模板签名在前 30 行内重定位表头（模板不存行号时可复用）。"""
    for idx in range(min(len(raw_rows), HEADER_SCAN_ROWS)):
        headers = [normalize_header(cell) for cell in raw_rows[idx]]
        if header_signature(headers) == signature:
            return idx, headers
    return None


__all__ = [
    "HEADER_SCAN_ROWS",
    "SUPPORTED_SUFFIXES",
    "ColumnRuleLike",
    "HeaderCandidate",
    "TableInspection",
    "inspect_table",
    "locate_by_signature",
    "read_rows",
    "rule_matches",
    "score_headers",
    "validate_file",
]
