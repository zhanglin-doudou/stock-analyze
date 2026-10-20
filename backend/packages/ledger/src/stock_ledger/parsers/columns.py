"""列别名表与日期格式探测（纯函数、无 I/O）。

券商账单表头千人千面（「成交日期」也可能写成「发生日期」），把别名集中在这里，
解析器只依赖字段名，不依赖具体表头——这是“列自动识别”的唯一真相来源。
日期格式探测把歧义（YYYY-DD-MM vs YYYY-MM-DD）显式暴露成警告码，而不是静默猜错。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from stock_contracts.enums import Side, WarningCode

# 字段 → 允许的表头别名
COLUMN_ALIASES: dict[str, tuple[str, ...]] = {
    "trade_date": ("成交日期", "交易日期", "发生日期", "清算日期", "日期"),
    "security_code": ("证券代码", "股票代码", "证券编码"),
    "security_name": ("证券名称", "股票名称", "证券简称"),
    "side": ("买卖标志", "操作", "交易类别", "买卖方向", "买卖"),
    "quantity": ("成交数量", "发生数量", "成交股数", "发生股数"),
    "price": ("成交价格", "发生价格", "成交均价"),
    "amount": ("成交金额", "发生金额", "发生额"),
    "commission": ("佣金", "手续费"),
    "stamp_tax": ("印花税", "交易印花税"),
    "other_fee": ("其他费", "过户费", "其他费用"),
    "cash_balance": ("资金余额", "余额"),
    "summary": ("摘要", "业务名称", "备注"),
    "account": ("资金账号", "股东代码", "客户号", "资金账户", "股东账号"),
}

REQUIRED_FIELDS: tuple[str, ...] = ("trade_date", "security_code", "quantity", "price")

# 已知格式：YYYYMMDD / YYYY-MM-DD / YYYY/M/D / YYYY-DD-MM（日的月份倒置）
DATE_FORMATS: tuple[str, ...] = ("YYYYMMDD", "YYYY-MM-DD", "YYYY/M/D", "YYYY-DD-MM")

_DIGITS8 = re.compile(r"\d{8}")
_DASHED = re.compile(r"(\d{4})-(\d{1,2})-(\d{1,2})")
_SLASHED = re.compile(r"(\d{4})/(\d{1,2})/(\d{1,2})")

_BUY_TOKENS: frozenset[str] = frozenset({"买", "买入", "证券买入", "b", "buy", "买出"})
_SELL_TOKENS: frozenset[str] = frozenset({"卖", "卖出", "证券卖出", "s", "sell"})


def normalize_header(raw: str) -> str:
    """表头归一：去 BOM/空白/全角空格，便于别名精确匹配。"""
    return raw.replace("\ufeff", "").replace(" ", "").replace("\u3000", "").strip()


def normalize_cell(raw: object) -> str:
    """单元格归一：None → 空串，其余去空白（金额里的千分位留给数值解析处理）。"""
    if raw is None:
        return ""
    return str(raw).strip()


@dataclass(slots=True)
class ColumnMapping:
    """列识别结果：原始表头 → 字段；以及未能识别的表头。"""

    columns: dict[str, str] = field(default_factory=dict)
    unresolved: list[str] = field(default_factory=list)

    def missing_required(self) -> list[str]:
        resolved = set(self.columns.values())
        return [name for name in REQUIRED_FIELDS if name not in resolved]

    def field_headers(self) -> dict[str, str]:
        """字段 → 原始表头（解析时按字段取值）。"""
        return {field: header for header, field in self.columns.items()}


def _match_field(header_norm: str) -> str | None:
    for name, aliases in COLUMN_ALIASES.items():
        for alias in aliases:
            norm_alias = normalize_header(alias)
            if header_norm == norm_alias or header_norm.startswith(norm_alias):
                return name
    return None


def map_columns(headers: list[str], *, mapping: dict[str, str] | None = None) -> ColumnMapping:
    """把表头映射到字段名；`mapping`（表头 → 字段）优先于自动识别。"""
    normalized_mapping = {normalize_header(key): value for key, value in (mapping or {}).items()}
    result = ColumnMapping()
    used: set[str] = set()
    for original in headers:
        norm = normalize_header(original)
        field_name = normalized_mapping.get(norm)
        if field_name is None:
            field_name = _match_field(norm)
        if field_name is None or field_name in used:
            result.unresolved.append(original)
            continue
        result.columns[original] = field_name
        used.add(field_name)
    return result


def _classify_parts(parts: list[tuple[int, int]]) -> str:
    """判断「中间段/末段」谁是月谁是日 → month_first / day_first / ambiguous / conflict。"""
    need_day_first = any(a > 12 for a, _ in parts)  # 中间段不可能是月 → 日在前
    need_month_first = any(b > 12 for _, b in parts)  # 末段不可能是日 → 月在前
    if need_day_first and need_month_first:
        return "conflict"
    if need_day_first:
        return "day_first"
    if need_month_first:
        return "month_first"
    return "ambiguous"


def detect_date_format(samples: list[str]) -> tuple[str | None, list[WarningCode]]:
    """从样本探测日期格式；歧义/冲突时返回警告码请调用方显式指定。"""
    cleaned = [s.strip() for s in samples if s and s.strip()]
    if not cleaned:
        return None, []
    if all(_DIGITS8.fullmatch(s) for s in cleaned):
        return "YYYYMMDD", []

    dashed = [_DASHED.fullmatch(s) for s in cleaned]
    if all(m is not None for m in dashed):
        parts = [(int(m.group(2)), int(m.group(3))) for m in dashed if m is not None]
        kind = _classify_parts(parts)
        if kind == "month_first":
            return "YYYY-MM-DD", []
        if kind == "day_first":
            return "YYYY-DD-MM", []
        if kind == "conflict":
            return None, [WarningCode.DATE_FORMAT_AMBIGUOUS]
        return "YYYY-MM-DD", [WarningCode.DATE_FORMAT_AMBIGUOUS]

    slashed = [_SLASHED.fullmatch(s) for s in cleaned]
    if all(m is not None for m in slashed):
        parts = [(int(m.group(2)), int(m.group(3))) for m in slashed if m is not None]
        kind = _classify_parts(parts)
        if kind == "day_first":
            return "YYYY-DD-MM", [WarningCode.DATE_FORMAT_AMBIGUOUS]
        if kind == "conflict":
            return None, [WarningCode.DATE_FORMAT_AMBIGUOUS]
        return "YYYY/M/D", ([WarningCode.DATE_FORMAT_AMBIGUOUS] if kind == "ambiguous" else [])

    return None, [WarningCode.DATE_FORMAT_AMBIGUOUS]


def parse_date_text(raw: str, date_format: str) -> tuple[int, int, int]:
    """把日期文本按指定格式拆成 (year, month, day)；格式不合法则抛 ValueError。"""
    text = raw.strip()
    if date_format == "YYYYMMDD":
        if not _DIGITS8.fullmatch(text):
            raise ValueError(f"非法日期: {raw!r}")
        return int(text[0:4]), int(text[4:6]), int(text[6:8])
    separator = "-" if "-" in text else "/"
    chunks = text.split(separator)
    if len(chunks) != 3:
        raise ValueError(f"非法日期: {raw!r}")
    year, first, second = int(chunks[0]), int(chunks[1]), int(chunks[2])
    if date_format == "YYYY-DD-MM":
        return year, second, first
    return year, first, second


def classify_side(raw: str) -> str | None:
    """买卖标志 → buy/sell；无法判断返回 None（由 `resolve_side` 继续兜底，不再隔离该行）。"""
    token = normalize_header(raw).lower()
    if token in _BUY_TOKENS:
        return "buy"
    if token in _SELL_TOKENS:
        return "sell"
    if "买" in token:
        return "buy"
    if "卖" in token:
        return "sell"
    return None


_SUMMARY_BUY_TOKEN = "证券买入"
_SUMMARY_SELL_TOKEN = "证券卖出"


def classify_side_from_summary(raw: str) -> str | None:
    """摘要兜底：含「证券买入」→ buy；含「证券卖出」→ sell；否则 None。"""
    text = normalize_header(raw)
    if _SUMMARY_BUY_TOKEN in text:
        return Side.BUY.value
    if _SUMMARY_SELL_TOKEN in text:
        return Side.SELL.value
    return None


def resolve_side(raw_side: str, raw_summary: str) -> str:
    """方向解析：买卖标志列优先 → 摘要兜底 → 默认 unknown。

    账单可能根本没有「买卖标志」列（或该单元格为空），此时从摘要里识别方向；
    两边都识别不出则回落为 `unknown`，不再整行隔离。
    """
    side = classify_side(raw_side)
    if side is not None:
        return side
    summary_side = classify_side_from_summary(raw_summary)
    if summary_side is not None:
        return summary_side
    return Side.UNKNOWN.value


__all__ = [
    "COLUMN_ALIASES",
    "DATE_FORMATS",
    "REQUIRED_FIELDS",
    "ColumnMapping",
    "classify_side",
    "classify_side_from_summary",
    "detect_date_format",
    "map_columns",
    "normalize_cell",
    "normalize_header",
    "parse_date_text",
    "resolve_side",
]
