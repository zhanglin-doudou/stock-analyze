"""导入 v2 的分析层：映射校验、类型聚合、费用合并校验与只读估算。

全部函数不写库（除仓储查询由 preview 编排），不依赖 API/worker，便于单测复放。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any, Final

from stock_contracts.constants import EPS_AMT
from stock_contracts.enums import BILL_CATEGORY_LABELS, BillCategory, Side, WarningCode
from stock_contracts.errors import ApiError, ErrorCode, FieldError
from stock_contracts.import_fields import (
    IMPORT_FIELD_CATALOG,
    L1_FIELDS,
    L1_TRADE_FIELDS,
    field_label,
)
from stock_core.values import Money

from stock_ledger.application.chain import group_rows_by_account
from stock_ledger.application.classify import category_after_amount_fallback
from stock_ledger.models import ImportBillType, SummaryRule
from stock_ledger.parsers.huatai import ParsedBill, ParsedRow
from stock_ledger.parsers.mapping import (
    MappingPlan,
    derive_type_key,
    normalize_type_key,
)
from stock_ledger.parsers.table import TableInspection

SYSTEM_CATEGORIES: Final[tuple[str, ...]] = tuple(category.value for category in BillCategory)
DERIVED_LABELS: Final[dict[str, str]] = {
    "derived:buy": "无摘要买入",
    "derived:sell": "无摘要卖出",
    "derived:zero": "零金额记录",
    "derived:unknown": "无法判定方向",
}
AMOUNT_TOLERANCE_RATIO: Final[Decimal] = Decimal("0.002")
FEE_ABS_LIMIT_UNITS: Final[int] = 10**14


def public_catalog() -> list[dict[str, object]]:
    return [item.as_catalog_item() for item in IMPORT_FIELD_CATALOG]


def public_categories() -> tuple[list[dict[str, str]], list[str]]:
    return (
        [
            {"code": category.value, "label": BILL_CATEGORY_LABELS[category.value]}
            for category in BillCategory
        ],
        list(SYSTEM_CATEGORIES),
    )


def resolve_broker(requested: str | None, detected: str) -> str:
    """`auto` 用检测结果；显式选择优先（用户可能更清楚自己的券商）。"""
    if requested is None or requested == "auto":
        return detected
    return requested


def completeness_issues(plan: MappingPlan) -> list[dict[str, Any]]:
    """映射完整性闸门：只有拿到 L1（+交易/资金二选一）才允许进入解析。"""
    fields = plan.mapped_fields()
    issues: list[dict[str, Any]] = []
    for name in L1_FIELDS:
        if name not in fields:
            issues.append(
                {
                    "field": name,
                    "code": "MAPPING_REQUIRED",
                    "message": f"必须映射{field_label(name)}",
                }
            )
    trade_fields = set(L1_TRADE_FIELDS) & fields
    if trade_fields:
        for name in L1_TRADE_FIELDS:
            if name not in fields:
                issues.append(
                    {
                        "field": name,
                        "code": "MAPPING_REQUIRED",
                        "message": f"交易账单必须映射{field_label(name)}",
                    }
                )
    elif "amount" not in fields or "summary" not in fields:
        # 纯资金流水才可省略代码/数量/价格；必须同时有净额与非交易摘要供判定。
        for name in ("amount", "summary"):
            if name not in fields:
                issues.append(
                    {
                        "field": name,
                        "code": "MAPPING_REQUIRED",
                        "message": f"纯资金流水必须映射{field_label(name)}",
                    }
                )
    # 注意：交易账单若缺摘要列，方向由 Category 派生、类型键按金额符号派生
    # （derived:buy/sell/zero），用户在类型步骤整步确认即可，故不阻断。
    return issues


def level_of(field_name: str) -> str:
    for item in IMPORT_FIELD_CATALOG:
        if item.field == field_name:
            return item.level
    return "L3"


@dataclass(slots=True)
class TypeGroup:
    """一个 `type_key` 的聚合结果（确认单一行）。"""

    type_key: str
    type_label: str
    raw_samples: list[str] = field(default_factory=list)
    row_count: int = 0
    amount_units: int = 0
    suggested_category: str | None = None
    suggested_rule_id: str | None = None
    source: str = "none"
    needs_confirmation: bool = True
    discard: bool = False
    category: str | None = None
    #: True=首次出现（import_bill_type 无记录）；False=用户此前已确认过该类型。
    is_new: bool = True

    def as_dict(self) -> dict[str, Any]:
        return {
            "type_key": self.type_key,
            "type_label": self.type_label,
            "row_count": self.row_count,
            "amount_sum": str(Money(self.amount_units)),
            "samples": self.raw_samples[:3],
            "suggested_category": self.suggested_category,
            "suggested_rule_id": self.suggested_rule_id,
            "source": self.source,
            "needs_confirmation": self.needs_confirmation,
            "discard": self.discard,
            "category": self.category,
            "is_new": self.is_new,
        }


def row_type_key(row: ParsedRow) -> tuple[str, str]:
    key = normalize_type_key(row.summary)
    if key:
        return key, row.summary or key
    balance = row.cash_balance_units or 0
    derived = derive_type_key(row.amount_units, balance)
    return derived, DERIVED_LABELS.get(derived, derived)


def aggregate_types(
    bill: ParsedBill,
    *,
    known_types: dict[str, ImportBillType],
    rules: list[SummaryRule],
) -> dict[str, TypeGroup]:
    """把明细按「原样摘要 / 派生键」聚合；记忆/摘要规则只预填，类型一律待用户整步确认。"""
    groups: dict[str, TypeGroup] = {}
    for row in bill.rows:
        key, label = row_type_key(row)
        group = groups.get(key)
        if group is None:
            group = TypeGroup(type_key=key, type_label=label[:120], needs_confirmation=True)
            groups[key] = group
        group.row_count += 1
        group.amount_units += row.amount_units
        if row.summary and row.summary not in group.raw_samples:
            group.raw_samples.append(row.summary)

    for key, group in groups.items():
        known = known_types.get(key)
        if known is not None:
            group.source = "import_type"
            group.discard = known.discarded
            group.is_new = False
            # E：即使命中历史记忆也不自动通过，仍需用户在类型步骤整步确认。
            group.needs_confirmation = True
            if known.discarded:
                # 废弃类型记忆不提供建议，让用户显式决定。
                group.category = None
                group.suggested_category = None
                continue
            # known.discarded=False → known.category 非 None（DB CheckConstraint 保证）
            assert known.category is not None, "ImportBillType: non-discarded 行必须有 category"
            # 历史记忆 category 也走零金额降级——同一 type_key 在本文件里全是登记行时
            # 应该降级为 broker_action，哪怕该记忆在过往真扣款文件里是 new_stock。
            group.category = category_after_amount_fallback(known.category, group.amount_units)
            group.suggested_category = group.category
            continue
        matched = _match_summary_rule(rules, group)
        if matched is not None:
            # 规则命中后也走零金额降级：group.amount_units==0 代表同一摘要的全部行
            # 都是登记动作，不应该按规则原始 category 预填 new_stock。
            final_category = category_after_amount_fallback(matched.category, group.amount_units)
            group.source = "summary_rule"
            group.suggested_category = final_category
            group.suggested_rule_id = matched.id
            group.category = final_category
            group.is_new = True
            # E：摘要规则只决定预填分类，不自动确认；确认范围限当前文件涉及的类型。
            group.needs_confirmation = True
    return groups


def _match_summary_rule(rules: list[SummaryRule], group: TypeGroup) -> SummaryRule | None:
    from stock_ledger.application.classify import match_rule

    for sample in group.raw_samples or [group.type_label]:
        matched = match_rule(rules, sample)
        if matched is not None:
            return matched
    return None


def resolutions_from_types(groups: dict[str, TypeGroup]) -> list[dict[str, Any]]:
    return [group.as_dict() for group in groups.values()]


def apply_type_resolutions(
    groups: dict[str, TypeGroup],
    resolutions: list[dict[str, Any]],
) -> dict[str, TypeGroup]:
    """合并用户提交的类型确认；未知键/非法分类显式拒绝，不静默忽略。"""
    keys = [str(item.get("type_key")) for item in resolutions]
    if len(keys) != len(set(keys)):
        raise ApiError(ErrorCode.IMPORT_MAPPING_INVALID, detail="同一账单类型不能提交多次")
    by_key = {str(item.get("type_key")): item for item in resolutions}
    unknown = sorted(set(by_key) - set(groups))
    if unknown:
        raise ApiError(
            ErrorCode.IMPORT_MAPPING_INVALID,
            detail=f"提交了不存在的账单类型: {unknown}",
        )
    missing = sorted(set(groups) - set(by_key))
    if missing:
        raise ApiError(
            ErrorCode.IMPORT_BILL_TYPES_UNRESOLVED,
            errors=[
                FieldError(key, "TYPE_REQUIRED", "必须选择分类或标记为废弃") for key in missing
            ],
            detail=f"还有 {len(missing)} 个账单类型未分类",
        )
    errors: list[FieldError] = []
    for key, group in groups.items():
        item = by_key[key]
        discard = bool(item.get("discard", False))
        category = item.get("category")
        if discard and category is not None:
            errors.append(FieldError(key, "CATEGORY_WITH_DISCARD", "分类与废弃不能同时选择"))
            continue
        if not discard:
            if category not in SYSTEM_CATEGORIES:
                errors.append(
                    FieldError(
                        key,
                        "INVALID_CATEGORY",
                        f"分类 {category!r} 不在 11 个系统分类内",
                    )
                )
                continue
            group.category = str(category)
            group.discard = False
        else:
            group.discard = True
            group.category = None
        group.needs_confirmation = False
        group.source = "user"
    if errors:
        raise ApiError(ErrorCode.IMPORT_CATEGORY_INVALID, errors=errors, detail="账单分类不合法")
    return groups


def unresolved_types(groups: dict[str, TypeGroup]) -> list[dict[str, Any]]:
    return [group.as_dict() for group in groups.values() if group.needs_confirmation]


def estimate_counts(
    bill: ParsedBill,
    groups: dict[str, TypeGroup],
    *,
    existing_keys: set[str],
) -> dict[str, int]:
    """只读估算：与 commit 同一顺序（dedupe → 废弃 → 待确认 → 插入）。

    隔离只统计解析期隔离行：类型待整步确认的行既不算写入也不算隔离——提交前必须
    确认全部类型，这些行不会落进隔离区。
    """
    seen: set[str] = set()
    inserted = skipped = unknown = discarded = 0
    for row in bill.rows:
        if row.dedupe_key in existing_keys or row.dedupe_key in seen:
            skipped += 1
            continue
        seen.add(row.dedupe_key)
        key, _ = row_type_key(row)
        group = groups.get(key)
        if group is None:
            unknown += 1
        elif group.discard:
            discarded += 1
        elif group.needs_confirmation:
            continue
        else:
            inserted += 1
    return {
        "inserted": inserted,
        "skipped": skipped,
        "unknown": unknown,
        "discarded": discarded,
        "quarantined": len(bill.quarantined) + unknown,
    }


def _parse_money_cell(cell: str) -> int | None:
    text = cell
    for noise in ("¥", "￥", "元", " ", ",", "\u3000"):
        text = text.replace(noise, "")
    if text in {"", "-", "--", "+", "—"}:
        return 0
    try:
        value = Decimal(text)
        return int((value * 100).to_integral_value())
    except (InvalidOperation, ValueError, OverflowError):
        return None


def verify_fee_merge(
    inspection: TableInspection, plan: MappingPlan, bill: ParsedBill
) -> tuple[dict[str, Any], list[str]]:
    """其他费多列合并的算术与余额链硬校验；全部金额在整数分域计算。"""
    fee_items = plan.fields().get("other_fee", ())
    if len(fee_items) <= 1:
        return {
            "fee_merge": {
                "applied": False,
                "columns": [item.column_key for item in fee_items],
                "status": "skipped",
                "checked_rows": 0,
                "skipped_rows": 0,
                "mismatches": [],
            }
        }, []
    key_to_idx = {key: idx for idx, key in enumerate(inspection.keys)}
    errors: list[FieldError] = []
    invalid = 0
    for row_index, row in enumerate(inspection.rows):
        if not any(cell for cell in row):
            continue
        line_no = inspection.header_row_index + row_index + 2
        values: list[int] = []
        row_invalid = False
        for item in fee_items:
            idx = key_to_idx.get(item.column_key)
            raw = row[idx] if idx is not None and idx < len(row) else ""
            value = _parse_money_cell(raw)
            if value is None:
                invalid += 1
                row_invalid = True
                if len(errors) < 20:
                    errors.append(
                        FieldError(
                            f"items.{item.column_key}",
                            "INVALID_FEE_CELL",
                            f"第 {line_no} 行费用值 {raw!r} 不是金额",
                        )
                    )
                continue
            values.append(value)
        if row_invalid:
            continue
        total = sum(values)
        if total < 0 or abs(total) > FEE_ABS_LIMIT_UNITS:
            invalid += 1
            if len(errors) < 20:
                errors.append(
                    FieldError(
                        f"items.{fee_items[0].column_key}",
                        "INVALID_FEE_TOTAL",
                        f"第 {line_no} 行合并费用 {total} 分为负数或超过上限",
                    )
                )
    if invalid:
        raise ApiError(
            ErrorCode.IMPORT_FEE_MERGE_INVALID,
            errors=errors,
            detail=f"其他费合并存在 {invalid} 个算术错误，请检查列语义",
        )

    columns = [item.column_key for item in fee_items]
    if "cash_balance" not in plan.mapped_fields():
        return {
            "fee_merge": {
                "applied": True,
                "columns": columns,
                "status": "skipped",
                "checked_rows": 0,
                "skipped_rows": len(bill.rows),
                "mismatches": [],
            }
        }, [WarningCode.FEE_MERGE_APPLIED.value, WarningCode.FEE_MERGE_UNVERIFIED.value]

    epsilon_units = Money.from_decimal(EPS_AMT).units
    mismatches: list[dict[str, Any]] = []
    checked = 0
    skipped = 0
    # 多资金子账户账单：每个子账户是一条独立余额链，跨账户切换不是断链。
    for _account, group_rows in group_rows_by_account(bill.rows):
        ordered = sorted(
            group_rows,
            key=lambda row: (
                row.trade_date,
                row.line_no,
                0 if row.side == Side.MISSING.value else 1,
            ),
        )
        if not ordered:
            continue
        anchor = ordered[0]
        for current in ordered[1:]:
            previous = anchor
            if previous.cash_balance_units is None or current.cash_balance_units is None:
                skipped += 1
                anchor = current
                continue
            expected = previous.cash_balance_units + current.amount_units
            actual = current.cash_balance_units
            delta = actual - expected
            checked += 1
            if abs(delta) > epsilon_units:
                mismatches.append(
                    {
                        "line_no": current.line_no,
                        "expected_units": expected,
                        "actual_units": actual,
                        "delta_units": delta,
                    }
                )
            anchor = current
    if mismatches:
        chain_errors = [
            FieldError(
                f"items.{fee_items[0].column_key}",
                "BALANCE_CHAIN_MISMATCH",
                (
                    f"第 {item['line_no']} 行期望余额 {Money(item['expected_units'])}，"
                    f"实际 {Money(item['actual_units'])}，差额 {Money(item['delta_units'])}"
                ),
            )
            for item in mismatches[:10]
        ]
        code = (
            ErrorCode.IMPORT_MAPPING_INVALID
            if plan.amount_signed()
            else ErrorCode.IMPORT_FEE_MERGE_VERIFY_FAILED
        )
        detail = (
            "金额列标记为已含费用，但余额链不符；请检查金额语义或账单是否缺行"
            if plan.amount_signed()
            else f"其他费合并后有 {len(mismatches)} 行余额链不符，请改单列或检查费用列"
        )
        raise ApiError(code, errors=chain_errors, detail=detail)
    return {
        "fee_merge": {
            "applied": True,
            "columns": columns,
            "status": "ok",
            "checked_rows": checked,
            "skipped_rows": skipped,
            "mismatches": [],
        }
    }, [WarningCode.FEE_MERGE_APPLIED.value]


def fee_merge_summary(plan: MappingPlan) -> dict[str, Any]:
    return {
        "columns": len(plan.fields().get("other_fee", ())),
        "applied": len(plan.fields().get("other_fee", ())) > 1,
    }


def stage_for(
    issues: list[dict[str, Any]], groups: dict[str, TypeGroup], *, parse_failed: bool
) -> str:
    if issues or parse_failed:
        return "mapping"
    if any(group.needs_confirmation for group in groups.values()):
        return "types"
    return "ready"


def suggestions_for_headers(
    inspection: TableInspection,
) -> list[dict[str, Any]]:
    """列明细：样例 + 建议字段 + 必填级别（前端映射表直接渲染）。"""
    from stock_ledger.parsers.mapping import suggest_items

    items, _unresolved = suggest_items(inspection.headers)
    by_key = {item.column_key: item for item in items}
    detail: list[dict[str, Any]] = []
    for index, header in enumerate(inspection.headers):
        key = inspection.keys[index]
        item = by_key.get(key)
        detail.append(
            {
                "index": index,
                "key": key,
                "name": header,
                "sample": (inspection.samples.get(key) or [""])[0],
                "samples": inspection.samples.get(key, []),
                "suggested_field": item.field if item else None,
                "suggested_options": item.options if item else {},
                "level": level_of(item.field) if item else None,
                "confidence": 1.0 if item else 0.0,
            }
        )
    return detail


def normalize_plan_items(plan: MappingPlan, inspection: TableInspection) -> MappingPlan:
    """按当前文件重算列键（表头移动/空白差异）并校验列存在。"""
    key_to_idx = {key: idx for idx, key in enumerate(inspection.keys)}
    unknown = [item.column_key for item in plan.items if item.column_key not in key_to_idx]
    if unknown:
        raise ApiError(
            ErrorCode.IMPORT_MAPPING_INVALID,
            errors=[
                FieldError("mapping", "unknown_column", f"列键 {key} 不存在") for key in unknown
            ],
            detail="映射引用了不存在的列",
        )
    return plan


__all__ = [
    "AMOUNT_TOLERANCE_RATIO",
    "DERIVED_LABELS",
    "SYSTEM_CATEGORIES",
    "TypeGroup",
    "aggregate_types",
    "apply_type_resolutions",
    "completeness_issues",
    "estimate_counts",
    "fee_merge_summary",
    "level_of",
    "normalize_plan_items",
    "public_catalog",
    "public_categories",
    "resolutions_from_types",
    "resolve_broker",
    "row_type_key",
    "stage_for",
    "suggestions_for_headers",
    "unresolved_types",
    "verify_fee_merge",
]
