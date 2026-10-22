"""解析器包：华泰对账单解析（纯函数）+ 列识别 + 去重键。

对外只暴露纯函数入口，方便预览/提交两阶段复用与属性测试。
"""

from __future__ import annotations

from stock_ledger.parsers.columns import (
    COLUMN_ALIASES,
    DATE_FORMATS,
    REQUIRED_FIELDS,
    ColumnMapping,
    detect_date_format,
    map_columns,
    parse_date_text,
)
from stock_ledger.parsers.dedupe import dedupe_key
from stock_ledger.parsers.huatai import ParsedBill, ParsedRow, QuarantineCandidate, parse_bill

__all__ = [
    "COLUMN_ALIASES",
    "DATE_FORMATS",
    "REQUIRED_FIELDS",
    "ColumnMapping",
    "ParsedBill",
    "ParsedRow",
    "QuarantineCandidate",
    "dedupe_key",
    "detect_date_format",
    "map_columns",
    "parse_bill",
    "parse_date_text",
]
