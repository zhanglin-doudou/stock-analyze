"""导入字段目录与券商别名（跨层唯一真相，纯常量、零 I/O）。

为什么放在 contracts：字段目录同时被解析器（ledger）、API 边界（PreviewOut.catalog）、
系统规则 seed（管理员 API/CLI）与前端下拉消费；放在任何下层都会迫使上层反向依赖。
本模块只描述「逻辑字段 ↔ 数据库列 ↔ 别名」，不含读表/校验等行为。

新链路（v2）的表头归一/别名一律用这里；legacy 解析器继续用
`stock_ledger.parsers.columns.COLUMN_ALIASES`，避免旧行为被静默改变。
两者唯一有意的差异是「备注」：legacy → `summary`，v2 → `note`（见设计附录 A）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

# ── 必填级别（设计 §3.1 / D-06）────────────────────────────────
LEVEL_DATE: Final[str] = "L1"
LEVEL_TRADE: Final[str] = "L1-trade"
LEVEL_CLASSIFY: Final[str] = "L2"
LEVEL_OPTIONAL: Final[str] = "L3"


@dataclass(frozen=True, slots=True)
class ImportField:
    """一个可用作映射目标的逻辑字段（对应数据库列）。"""

    field: str
    db_column: str
    label: str
    level: str
    mergeable: bool
    value_type: str

    def as_catalog_item(self) -> dict[str, object]:
        """API-010 `catalog[]` 的对外形状（前端不硬编码字段表）。"""
        return {
            "field": self.field,
            "db_column": self.db_column,
            "label": self.label,
            "level": self.level,
            "mergeable": self.mergeable,
            "value_type": self.value_type,
        }


#: 字段目录（顺序即前端「必填 / 常用 / 其他」分组的自然顺序）。
IMPORT_FIELD_CATALOG: Final[tuple[ImportField, ...]] = (
    ImportField("trade_date", "ledger.trade.trade_date", "成交日期", LEVEL_DATE, False, "date"),
    ImportField(
        "security_code", "ledger.trade.security_code", "证券代码", LEVEL_TRADE, False, "text"
    ),
    ImportField("quantity", "ledger.trade.quantity_units", "成交数量", LEVEL_TRADE, False, "qty"),
    ImportField("price", "ledger.trade.price_units", "成交价格", LEVEL_TRADE, False, "price"),
    ImportField(
        "security_name", "ledger.trade.security_name", "证券名称", LEVEL_OPTIONAL, False, "text"
    ),
    ImportField("side", "ledger.trade.side", "买卖标志", LEVEL_CLASSIFY, False, "buy_sell"),
    ImportField("amount", "ledger.trade.amount_units", "发生金额", LEVEL_CLASSIFY, False, "money"),
    ImportField(
        "commission", "ledger.trade.commission_units", "佣金", LEVEL_OPTIONAL, False, "money"
    ),
    ImportField(
        "stamp_tax", "ledger.trade.stamp_tax_units", "印花税", LEVEL_OPTIONAL, False, "money"
    ),
    ImportField(
        "other_fee", "ledger.trade.other_fee_units", "其他费", LEVEL_OPTIONAL, True, "money"
    ),
    ImportField(
        "cash_balance",
        "ledger.trade.cash_balance_units",
        "资金余额",
        LEVEL_OPTIONAL,
        False,
        "money",
    ),
    ImportField(
        "money_fund_balance",
        "ledger.trade.money_fund_balance_units",
        "货基余额",
        LEVEL_OPTIONAL,
        False,
        "money",
    ),
    ImportField("summary", "ledger.trade.summary", "账单类型/摘要", LEVEL_CLASSIFY, False, "text"),
    ImportField("note", "ledger.trade.note", "备注", LEVEL_OPTIONAL, False, "text"),
    ImportField(
        "account", "ledger.trade.account_masked", "资金账号", LEVEL_OPTIONAL, False, "text"
    ),
)

#: 字段键集合（校验 target_field 是否属于字段目录）。
FIELD_KEYS: Final[frozenset[str]] = frozenset(item.field for item in IMPORT_FIELD_CATALOG)

#: 允许「多列 → 一列」合并的字段（设计 D-08）：唯一例外是 `other_fee`。
MERGEABLE_FIELDS: Final[frozenset[str]] = frozenset({"other_fee"})

#: `other_fee` 合并列数上限（2~8；1 列等同普通映射）。
OTHER_FEE_MAX_COLUMNS: Final[int] = 8

#: L1/L1-trade/L2 级别分组（校验器按此判定必填）。
L1_FIELDS: Final[tuple[str, ...]] = ("trade_date",)
L1_TRADE_FIELDS: Final[tuple[str, ...]] = ("security_code", "quantity", "price")
L2_FIELDS: Final[tuple[str, ...]] = ("side", "amount", "summary")

#: 字段 → 允许的表头别名（v2 新链路）。必须覆盖 legacy `COLUMN_ALIASES`
#: 的全部别名（「备注」除外，见模块 docstring 与附录 A 覆盖闸门单测）。
FIELD_ALIASES: Final[dict[str, tuple[str, ...]]] = {
    "trade_date": ("成交日期", "交易日期", "发生日期", "清算日期", "日期"),
    "security_code": ("证券代码", "股票代码", "证券编码"),
    "security_name": ("证券名称", "股票名称", "证券简称"),
    "side": ("买卖标志", "操作", "交易类别", "交易类型", "买卖方向", "买卖"),
    "quantity": ("成交数量", "发生数量", "成交股数", "发生股数"),
    "price": ("成交价格", "发生价格", "成交均价"),
    # amount 的库列语义是「带符号净现金流」（label 即「发生金额」），故 signed 别名排最前：
    # 同一账单同时有「成交金额」毛额与「发生金额」净额时，必须让「发生金额」拿到 amount，
    # 否则非交易资金行（银证转入/利息归本）的毛额恒为 0，现金流会被静默丢弃。
    "amount": ("发生金额", "发生额", "成交金额"),
    "commission": ("佣金", "手续费"),
    "stamp_tax": ("印花税", "交易印花税"),
    "other_fee": ("其他费", "过户费", "其他费用"),
    "cash_balance": ("资金余额", "余额"),
    "money_fund_balance": ("货基余额", "货币基金余额", "基金余额"),
    "summary": ("摘要", "业务名称", "业务类型"),
    "note": ("备注",),
    # account 只认资金账号类列（含「帐」异体字）。股东代码/股东账号是「沪/深市场股东号」，
    # 一张账单天然会有两个，识别成账户会把单账户账单误判成多账户；客户号同理不当作账户。
    "account": ("资金账号", "资金帐号", "资金账户", "资金帐户"),
}

#: 原生「带符号净现金流」的金额表头：命中即预置 `options.amount_signed=true`。
#: 「成交金额」是毛额，必须按方向 ± 费用换算（设计 §3.3 规则 6）。
SIGNED_AMOUNT_ALIASES: Final[frozenset[str]] = frozenset({"发生金额", "发生额"})

#: 参与券商自动识别打分的「华泰 12 个精确表头」（附录 A ✅ 项）。
HUATAI_FINGERPRINT_HEADERS: Final[tuple[str, ...]] = (
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
)

#: 券商展示名（代码常量；P1 可改为表驱动）。
BROKER_LABELS: Final[dict[str, str]] = {
    "auto": "自动识别",
    "generic": "通用",
    "huatai": "华泰证券",
}

#: v2 支持的日期格式（与 legacy `DATE_FORMATS` 同口径）。
DATE_FORMATS: Final[tuple[str, ...]] = (
    "YYYYMMDD",
    "YYYY-MM-DD",
    "YYYY/M/D",
    "YYYY-DD-MM",
)

#: `options` 里允许出现的键（MVP 只有金额语义一个开关）。
AMOUNT_OPTION_KEYS: Final[frozenset[str]] = frozenset({"amount_signed"})


def field_label(field: str) -> str:
    for item in IMPORT_FIELD_CATALOG:
        if item.field == field:
            return item.label
    return field


def broker_label(code: str) -> str:
    return BROKER_LABELS.get(code, code)


def alias_options(field: str, header: str) -> dict[str, bool]:
    """系统建议的列选项：`发生金额/发生额` 预置带符号，其余金额列按毛额。"""
    if field == "amount" and header in SIGNED_AMOUNT_ALIASES:
        return {"amount_signed": True}
    return {}


__all__ = [
    "AMOUNT_OPTION_KEYS",
    "BROKER_LABELS",
    "DATE_FORMATS",
    "FIELD_ALIASES",
    "FIELD_KEYS",
    "HUATAI_FINGERPRINT_HEADERS",
    "IMPORT_FIELD_CATALOG",
    "L1_FIELDS",
    "L1_TRADE_FIELDS",
    "L2_FIELDS",
    "LEVEL_CLASSIFY",
    "LEVEL_DATE",
    "LEVEL_OPTIONAL",
    "LEVEL_TRADE",
    "MERGEABLE_FIELDS",
    "OTHER_FEE_MAX_COLUMNS",
    "SIGNED_AMOUNT_ALIASES",
    "ImportField",
    "alias_options",
    "broker_label",
    "field_label",
]
