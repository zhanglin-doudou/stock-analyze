"""内置系统导入模板（只读，永不写库）。

系统模板固化「券商导出格式」的列对应与账单类型映射，供用户在没有自己的模板时
一键套用。用户一旦改动，就在提交成功时 copy-on-write 成自己的 `import_template`
行；系统模板本身不随用户改动变化。

预设里的 `bill_types` 只带 `type_key → category/discarded`，不引用任何数据库 id：
落库时按当前用户的 `import_bill_type` upsert 后再写 id，因此跨用户安全。

数据来源：1234@dev.com 在 5432 实际确认过的华泰模板（11 条列对应 + 18 个类型）。
`signature` 与空白列修复后的 13 列表头一致（修复前模板记录的 `header_count=24`
只是计数陈旧，签名本身未变）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

SYSTEM_TEMPLATE_ID_PREFIX = "sys:"


@dataclass(frozen=True, slots=True)
class PresetBillType:
    """预设里的一个账单类型（不引用数据库 id）。"""

    type_key: str
    category: str | None
    discarded: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": None,
            "type_key": self.type_key,
            "type_label": self.type_key,
            "category": self.category,
            "discarded": self.discarded,
            "invalid": False,
        }


@dataclass(frozen=True, slots=True)
class SystemTemplate:
    """只读系统模板：列对应 + 账单类型映射。"""

    key: str
    broker: str
    signature: str
    header_count: int
    date_format: str | None
    items: tuple[dict[str, Any], ...]
    ignored: tuple[str, ...]
    bill_types: tuple[PresetBillType, ...]
    preset_version: int = 1

    @property
    def template_id(self) -> str:
        return f"{SYSTEM_TEMPLATE_ID_PREFIX}{self.key}"


_HUATAI_ITEMS: tuple[dict[str, Any], ...] = (
    {"field": "trade_date", "options": {}, "column_key": "70d0c1b33626ba4b"},
    {"field": "security_code", "options": {}, "column_key": "70e5179c3767f431"},
    {"field": "security_name", "options": {}, "column_key": "15415c08e7fe4afd"},
    {"field": "summary", "options": {}, "column_key": "21c04b2eeeb431f7"},
    {"field": "quantity", "options": {}, "column_key": "5bffd9af3eedffae"},
    {"field": "price", "options": {}, "column_key": "e82fc6d14d555c5e"},
    {"field": "commission", "options": {}, "column_key": "41e2d8e21e178e95"},
    {"field": "stamp_tax", "options": {}, "column_key": "45d556d5233a2b23"},
    {"field": "other_fee", "options": {}, "column_key": "6c6ce0ad91c7ea57"},
    {"field": "amount", "options": {"amount_signed": True}, "column_key": "d518bf4fa35b4acb"},
    {"field": "cash_balance", "options": {}, "column_key": "75988ede86796cd4"},
)

_HUATAI_BILL_TYPES: tuple[PresetBillType, ...] = (
    PresetBillType("利息归本", "interest"),
    PresetBillType("市值申购中签", "broker_action"),
    PresetBillType("托管转出", "broker_action"),
    PresetBillType("新股入帐", "broker_action"),
    PresetBillType("新股申购确认缴款", "new_stock"),
    PresetBillType("申购确认", "buy"),
    PresetBillType("红利发放", "interest"),
    PresetBillType("股息入帐", "dividend"),
    PresetBillType("股息红利税补缴", "dividend"),
    PresetBillType("证券买入", "buy"),
    PresetBillType("证券卖出", "sell"),
    PresetBillType("资管转让资金上账", "sell"),
    PresetBillType("赎回确认", "sell"),
    PresetBillType("银行转取", "transfer"),
    PresetBillType("银行转存", "transfer"),
    PresetBillType("非交易过户确认", "broker_action"),
    PresetBillType("[省心投]申购确认", "transfer"),
    PresetBillType("[省心投]红利发放", "interest"),
)

SYSTEM_TEMPLATES: tuple[SystemTemplate, ...] = (
    SystemTemplate(
        key="huatai",
        broker="huatai",
        signature="86a2e1d34c53b1c9",
        header_count=13,
        date_format="YYYYMMDD",
        items=_HUATAI_ITEMS,
        ignored=("9677f386bdcdd9c4", "56c13d96421589a4"),
        bill_types=_HUATAI_BILL_TYPES,
    ),
)


def find_system_template(*, key: str | None = None, signature: str | None = None) -> SystemTemplate | None:
    """按 key 或文件签名查系统模板；两者都不命中返回 None。"""
    for template in SYSTEM_TEMPLATES:
        if key is not None and template.key == key:
            return template
        if signature is not None and template.signature == signature:
            return template
    return None


def system_template_by_id(template_id: str | None) -> SystemTemplate | None:
    if not template_id or not template_id.startswith(SYSTEM_TEMPLATE_ID_PREFIX):
        return None
    return find_system_template(key=template_id[len(SYSTEM_TEMPLATE_ID_PREFIX) :])


def system_template_payload(template: SystemTemplate) -> dict[str, Any]:
    """系统模板 → 预览/列表同构 payload（`source="system"`）。"""
    return {
        "template_id": template.template_id,
        "broker": template.broker,
        "signature": template.signature,
        "header_count": template.header_count,
        "date_format": template.date_format,
        "items": [dict(item) for item in template.items],
        "ignored": list(template.ignored),
        "origin_batch_id": None,
        "last_batch_id": None,
        "created_at": None,
        "updated_at": None,
        "source": "system",
        "preset_key": template.key,
        "preset_version": template.preset_version,
        "has_user_overrides": False,
        "bill_type_ids": [],
        "bill_types": [item.as_dict() for item in template.bill_types],
        "invalid_type_ids": [],
    }
