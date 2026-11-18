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
    # account 只认资金账号类列：股东代码/股东账号是沪/深股东号，一张账单会有两个，
    # 当作账户会误拆对账链；客户号也不是资金账号（口径与 v2 FIELD_ALIASES 一致）。
    "account": ("资金账号", "资金帐号", "资金账户", "资金帐户"),
}

REQUIRED_FIELDS: tuple[str, ...] = ("trade_date", "security_code", "quantity", "price")

#: 需要脱敏的账户类表头：即使这些列不再映射 `account`（如沪/深股东代码），
#: 其明文也不能落到隔离区 raw 或预览 sample 里，源头统一抹成 MD5。
SENSITIVE_ACCOUNT_ALIASES: tuple[str, ...] = (
    "资金账号",
    "资金帐号",
    "资金账户",
    "资金帐户",
    "股东代码",
    "股东账号",
    "客户号",
)

# 金额列语义：这些表头原生就是「带符号的发生金额/净现金流」（买入为负、卖出为正、已含费用），
# 解析器直接使用；其余（典型是「成交金额」毛额）必须按方向 + 费用换算成带符号净额。
SIGNED_AMOUNT_HEADERS: frozenset[str] = frozenset({"发生金额", "发生额"})

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


_INVISIBLE_CHARS = frozenset({"\u200b", "\u200c", "\u200d", "\ufeff"})


def strip_all_whitespace(text: str) -> str:
    """删除全部空白与零宽字符（不替换为空格）。

    账单摘要里的空白几乎都是折行/对齐产物（如「股息红利税\\n补缴」），删除后
    中文语义不变；统一「删除」而不是「替换为单个空格」，避免下游还要再处理
    一个额外的分隔符。规则匹配与类型 key 共用这一口径。
    """
    return "".join(char for char in text if char not in _INVISIBLE_CHARS and not char.isspace())


def normalize_cell(raw: object) -> str:
    """单元格归一：None → 空串，其余去空白（金额里的千分位留给数值解析处理）。"""
    if raw is None:
        return ""
    return str(raw).strip()


def normalize_summary(raw: str) -> str:
    """摘要归一：删除换行/制表/空格/全角空格/零宽字符。

    券商常把摘要折行导出（如「[省心投]红\\n利发放」「新股申购确\\n认缴款」），
    不归一会导致方向识别与分类规则全部失配，整片行被误判成「未知摘要」隔离。
    解析入口统一调用本函数，保证落库摘要、类型 key、规则匹配、模板比对同一口径。
    """
    return strip_all_whitespace(normalize_header(raw))


def is_signed_amount_header(raw: str | None) -> bool:
    """金额列是否原生带符号（「发生金额/发生额」）。

    表头缺失（金额按量×价推导）或映射覆盖成未知表头时返回 False，按毛额处理；
    毛额列若出现负数，解析器仍会按「已带符号净额」兜底（毛额不可能为负）。
    """
    return raw is not None and normalize_header(raw) in SIGNED_AMOUNT_HEADERS


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
    """把表头映射到字段名；`mapping`（表头 → 字段）优先于自动识别。

    `amount` 字段特殊处理：库列语义是「带符号净现金流」，当账单同时有「成交金额」
    （毛额）与「发生金额/发生额」（带符号净额）时，必须让带符号列拿到 `amount`，
    否则非交易资金行（银证转入/利息归本/红股派息等）的毛额恒为 0，现金流会被静默
    丢弃、余额链断链。口径与 v2 `suggest_items`（`FIELD_ALIASES`）一致。
    """
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

    # amount 列优先带符号净额：若当前 amount 落在毛额列（如「成交金额」），且 unresolved
    # 里有带符号列（「发生金额/发生额」），则交换——让带符号列拿 amount，毛额列进 unresolved。
    amount_header = next(
        (h for h, f in result.columns.items() if f == "amount"),
        None,
    )
    if amount_header is not None and not is_signed_amount_header(amount_header):
        signed_idx = next(
            (idx for idx, h in enumerate(result.unresolved) if is_signed_amount_header(h)),
            None,
        )
        if signed_idx is not None:
            signed_header = result.unresolved.pop(signed_idx)
            del result.columns[amount_header]
            result.columns[signed_header] = "amount"
            result.unresolved.append(amount_header)
    # 重建 unresolved 为表头顺序，避免换出列位置漂移导致 legacy 与 v2 计划不一致。
    resolved = set(result.columns)
    result.unresolved = [h for h in headers if h not in resolved]
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


# 非交易类（资金类）摘要关键词：这些行没有真实证券代码，导入时用 CASH 占位。
# 方向不再由摘要推断——统一在分类阶段由 Category 决定（buy/new_stock→买入，sell→卖出）。
# 词表与分类系统规则（repositories/rules.py）保持同源，避免「解析器认识、分类器不认识」
# 导致的真实资金行被误隔离（P1-8 / G3）。
NON_TRADE_SUMMARY_KEYWORDS: tuple[str, ...] = (
    "银行转存",
    "银行转取",
    "银证转入",
    "银证转出",
    "跨行转入",
    "跨行转出",
    "带资金补登",
    "资产账户币种开户",
    "股息入帐",
    "利息归本",
    "股息红利税",
    "转存管转入",
    "转存管转出",
    "资管转让资金上账",
    # 打新/可转债申购的资金腿：冻结/缴款是资金流出，解冻/还款/退款是资金退回。
    # 这些行证券代码可为空，纳入非交易词汇后用 CASH 占位导入，避免被当成字段缺失行隔离、
    # 打断资金余额链。
    "申购冻结",
    "申购解冻",
    "申购还款",
    "申购退款",
    "中签退款",
    # 「市值认购资金扣收」：按市值认购可转债/新股的缴款腿，华泰账单里证券代码为空，
    # 只有发生金额（负）与资金余额。与上面的申购资金腿同构，用 CASH 占位导入。
    "市值认购资金扣收",
)

# 资金划转（银证转账 / 转存管）摘要关键词：分类规则与方向识别共用同一词表。
# 「银行转存/银行转取/转存管转入/转存管转出」直接取自上面的非交易词汇；
# 「银行转证券/证券转银行」是一期历史摘要（合成夹具仍使用），保留兼容。
TRANSFER_SUMMARY_KEYWORDS: tuple[str, ...] = (
    "银行转证券",
    "证券转银行",
    "银行转存",
    "银行转取",
    "转存管转入",
    "转存管转出",
    "资管转让资金上账",
    # 银证转账的另一种摘要写法（华泰「银证转入/银证转出」）：与解析词表对称，避免
    # 「解析器认识、分类器不认识」导致真实资金行在分类阶段被隔离。
    "银证转入",
    "银证转出",
)


def is_non_trade_summary(raw: str) -> bool:
    """摘要是否属于非交易类（资金类）业务——这些行用 CASH 占位导入。

    方向不再由此判定，统一在分类阶段由 Category 决定。
    """
    text = normalize_summary(raw)
    return any(keyword in text for keyword in NON_TRADE_SUMMARY_KEYWORDS)


def resolve_side(raw_side: str, raw_summary: str) -> str:
    """方向解析：只认「买卖标志」列；无列或空值时回落 unknown。

    买入/卖出方向不再由摘要推断——分类阶段由 Category 决定（buy/new_stock→买入，
    sell→卖出）。保留买卖标志列仅用于毛额账单（仅有「成交金额」无「发生金额」时）
    的金额符号换算。
    """
    side = classify_side(raw_side)
    if side is not None:
        return side
    return Side.UNKNOWN.value


__all__ = [
    "COLUMN_ALIASES",
    "DATE_FORMATS",
    "NON_TRADE_SUMMARY_KEYWORDS",
    "REQUIRED_FIELDS",
    "SIGNED_AMOUNT_HEADERS",
    "TRANSFER_SUMMARY_KEYWORDS",
    "ColumnMapping",
    "classify_side",
    "detect_date_format",
    "is_non_trade_summary",
    "is_signed_amount_header",
    "map_columns",
    "normalize_cell",
    "normalize_header",
    "normalize_summary",
    "parse_date_text",
    "resolve_side",
    "strip_all_whitespace",
]
