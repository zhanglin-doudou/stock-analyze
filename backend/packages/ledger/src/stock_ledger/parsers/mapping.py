"""映射计划（MappingPlan）：v2 解析的唯一输入契约（纯函数、无 I/O）。

设计 `docs/architecture/2026-11-13-import-mapping-refactor.md` §3.3 / §4.1：
- 列用 `column_key`（表头归一后的 sha256 前 16 位）标识，**不存行号**，列顺序变化无感；
- `header_row_index` 单独存，表头移动可重定位；
- `items[]` 决定「哪一列 → 哪个字段」，`other_fee` 允许多列合并（D-08）；
- 计划校验只做形状校验（字段白名单/重复/合并上限/选项），业务必填由应用层按账单类型判定。
"""

from __future__ import annotations

import hashlib
import json
import unicodedata
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from typing import Any, Final

from stock_contracts.errors import ApiError, ErrorCode, FieldError
from stock_contracts.import_fields import (
    AMOUNT_OPTION_KEYS,
    FIELD_ALIASES,
    FIELD_KEYS,
    MERGEABLE_FIELDS,
    OTHER_FEE_MAX_COLUMNS,
    alias_options,
)

from stock_ledger.parsers.columns import (
    ColumnMapping,
    map_columns,
    normalize_header,
)

#: 账单类型键前缀：摘要缺失/为空时用方向 + 金额符号派生（设计 D-13）。
DERIVED_TYPE_PREFIX: Final[str] = "derived:"
TYPE_KEY_MAX_LEN: Final[int] = 64


def normalize_header_v2(raw: str) -> str:
    """v2 表头归一：去 BOM/空白/全角空格并小写，供规则匹配与签名使用。"""
    return normalize_header(raw).lower()


def column_key(header: str) -> str:
    """列键：归一表头 → sha256 前 16 位；同一文件内重复表头由 `column_keys` 加后缀。"""
    digest = hashlib.sha256(normalize_header_v2(header).encode("utf-8")).hexdigest()
    return digest[:16]


def column_keys(headers: list[str]) -> list[str]:
    """按列顺序生成去重列键；重复表头依次加 `-2`、`-3` 后缀。"""
    seen: dict[str, int] = {}
    keys: list[str] = []
    for header in headers:
        base = column_key(header)
        seen[base] = seen.get(base, 0) + 1
        keys.append(base if seen[base] == 1 else f"{base}-{seen[base]}")
    return keys


def header_signature(headers: list[str]) -> str:
    """文件签名：表头归一后按序 join 的 sha256 前 16 位（模板按此绑定）。"""
    joined = "|".join(normalize_header_v2(header) for header in headers)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True, slots=True)
class MappingItem:
    """一条「列 → 字段」映射。`options` 目前只允许 `amount_signed`。"""

    column_key: str
    field: str
    options: dict[str, Any] = dataclass_field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {"column_key": self.column_key, "field": self.field, "options": dict(self.options)}

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> MappingItem:
        return cls(
            column_key=str(raw.get("column_key", "")),
            field=str(raw.get("field", "")),
            options=dict(raw.get("options") or {}),
        )


@dataclass(frozen=True, slots=True)
class MappingPlan:
    """一张账单的完整映射计划（草稿/提交/模板三处同构）。"""

    header_row_index: int
    items: tuple[MappingItem, ...] = ()
    ignored: tuple[str, ...] = ()

    def fields(self) -> dict[str, tuple[MappingItem, ...]]:
        grouped: dict[str, list[MappingItem]] = {}
        for item in self.items:
            grouped.setdefault(item.field, []).append(item)
        return {name: tuple(items) for name, items in grouped.items()}

    def mapped_fields(self) -> frozenset[str]:
        return frozenset(item.field for item in self.items)

    def amount_signed(self) -> bool:
        """金额列是否按原生带符号净额处理（`options.amount_signed=true`）。"""
        for item in self.items:
            if item.field == "amount" and item.options.get("amount_signed"):
                return True
        return False

    def as_items(self) -> list[dict[str, Any]]:
        return [item.as_dict() for item in self.items]

    def as_dict(self) -> dict[str, Any]:
        return {
            "header_row_index": self.header_row_index,
            "items": self.as_items(),
            "ignored": list(self.ignored),
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> MappingPlan:
        return cls(
            header_row_index=int(raw.get("header_row_index", 0)),
            items=tuple(MappingItem.from_dict(item) for item in raw.get("items") or []),
            ignored=tuple(str(key) for key in raw.get("ignored") or []),
        )

    def fingerprint(self) -> str:
        """映射指纹：驱动重导检测与「模板命中但映射变了」比较。"""
        canonical = json.dumps(
            {
                "header_row_index": self.header_row_index,
                "items": sorted(
                    (item.column_key, item.field, sorted(item.options.items()))
                    for item in self.items
                ),
                "ignored": sorted(self.ignored),
            },
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:32]


def validate_plan(plan: MappingPlan) -> None:
    """形状校验；失败抛 `IMPORT_MAPPING_INVALID`（RFC 7807 errors[] 指到具体列）。"""
    errors: list[FieldError] = []
    if plan.header_row_index < 0:
        errors.append(FieldError("header_row_index", "invalid_header_row", "表头行号不能为负数"))
    if not plan.items:
        errors.append(FieldError("mapping", "empty_mapping", "至少映射一列"))
    seen_columns: set[str] = set()
    field_counts: dict[str, int] = {}
    for item in plan.items:
        if not item.column_key:
            errors.append(FieldError("mapping", "missing_column_key", "列键不能为空"))
        if item.column_key in seen_columns:
            errors.append(
                FieldError("mapping", "duplicate_column", f"列 {item.column_key} 被重复映射")
            )
        seen_columns.add(item.column_key)
        if item.field not in FIELD_KEYS:
            errors.append(FieldError("mapping", "unknown_field", f"未知字段: {item.field}"))
            continue
        field_counts[item.field] = field_counts.get(item.field, 0) + 1
        unknown_options = set(item.options) - AMOUNT_OPTION_KEYS
        if unknown_options:
            errors.append(
                FieldError(
                    "mapping",
                    "invalid_option",
                    f"字段 {item.field} 含不支持的选项: {sorted(unknown_options)}",
                )
            )
        if (
            item.field == "amount"
            and "amount_signed" in item.options
            and not isinstance(item.options["amount_signed"], bool)
        ):
            errors.append(FieldError("mapping", "invalid_option", "amount_signed 必须是布尔值"))
    for name, count in field_counts.items():
        if count > 1 and name not in MERGEABLE_FIELDS:
            errors.append(
                FieldError(
                    "mapping",
                    "duplicate_field",
                    f"字段 {name} 只能映射一列（发现 {count} 列）",
                )
            )
    if field_counts.get("other_fee", 0) > OTHER_FEE_MAX_COLUMNS:
        errors.append(
            FieldError(
                "mapping",
                "too_many_columns",
                f"other_fee 最多合并 {OTHER_FEE_MAX_COLUMNS} 列",
            )
        )
    overlap = set(plan.ignored) & seen_columns
    if overlap:
        errors.append(
            FieldError("mapping", "ignored_column_mapped", f"列同时被忽略与映射: {sorted(overlap)}")
        )
    if errors:
        raise ApiError(ErrorCode.IMPORT_MAPPING_INVALID, errors=errors, detail="映射计划不合法")


def merge_fee_units(units: list[int]) -> int:
    """合并费用（设计 D-08）：全部在 int 域求和，绝不引入 float。"""
    total = 0
    for unit in units:
        total += unit
    return total


def normalize_type_key(summary: str | None) -> str:
    """账单类型键：NFKC + 去零宽/空白 + ASCII 小写，截断 64（D-13）。"""
    if not summary:
        return ""
    text = unicodedata.normalize("NFKC", summary)
    cleaned = "".join(
        char
        for char in text
        if char not in {"\u200b", "\u200c", "\u200d", "\ufeff"} and not char.isspace()
    )
    ascii_lower = "".join(char.lower() if "A" <= char <= "Z" else char for char in cleaned)
    return ascii_lower[:TYPE_KEY_MAX_LEN]


def derive_type_key(amount_units: int, balance_units: int = 0) -> str:
    """无摘要时按金额符号派生类型键（D-13）：负=买入、正=卖出、零=zero。"""
    if amount_units == 0 and balance_units == 0:
        return f"{DERIVED_TYPE_PREFIX}zero"
    if amount_units < 0:
        return f"{DERIVED_TYPE_PREFIX}buy"
    if amount_units > 0:
        return f"{DERIVED_TYPE_PREFIX}sell"
    return f"{DERIVED_TYPE_PREFIX}unknown"


def plan_from_legacy_mapping(
    headers: list[str], header_row_index: int, mapping: dict[str, str] | None
) -> MappingPlan:
    """旧预览（只有 表头→字段 字典）→ MappingPlan，用于 legacy 预览续接 v2 草稿。

    复用 legacy `map_columns`，保证与旧解析结果逐字段一致（包括「同一字段只取首列」
    与未识别列的顺序）。
    """
    found: ColumnMapping = map_columns(headers, mapping=mapping)
    keys = column_keys(headers)
    items: list[MappingItem] = []
    for idx, header in enumerate(headers):
        field_name = found.columns.get(header)
        if field_name is None:
            continue
        items.append(
            MappingItem(
                column_key=keys[idx],
                field=field_name,
                options=alias_options(field_name, normalize_header(header)),
            )
        )
    ignored = tuple(key for idx, key in enumerate(keys) if headers[idx] not in found.columns)
    return MappingPlan(header_row_index=header_row_index, items=tuple(items), ignored=ignored)


def _alias_index(field_name: str, header_norm: str) -> int | None:
    for idx, alias in enumerate(FIELD_ALIASES.get(field_name, ())):
        if header_norm == normalize_header_v2(alias):
            return idx
    return None


def suggest_items(headers: list[str]) -> tuple[list[MappingItem], list[str]]:
    """按字段目录别名给出初始映射建议；返回 `(items, 未识别列键)`。

    规则：同一字段只取「别名序号最靠前」的一列；`other_fee` 例外，可自动合并多列。
    """
    keys = column_keys(headers)
    norm_headers = [normalize_header_v2(header) for header in headers]
    best: dict[str, tuple[int, int]] = {}  # field -> (alias_index, column_index)
    for col_idx, norm in enumerate(norm_headers):
        for field_name in FIELD_ALIASES:
            alias_idx = _alias_index(field_name, norm)
            if alias_idx is None:
                continue
            current = best.get(field_name)
            if current is None or (alias_idx, col_idx) < current:
                best[field_name] = (alias_idx, col_idx)
    items: list[MappingItem] = []
    used: set[int] = set()
    for field_name, (_, col_idx) in sorted(best.items(), key=lambda pair: pair[1][1]):
        items.append(
            MappingItem(
                column_key=keys[col_idx],
                field=field_name,
                options=alias_options(field_name, normalize_header(headers[col_idx])),
            )
        )
        used.add(col_idx)
    # other_fee 自动合并所有别名命中列（上限 8），避免「过户费/其他费」漏加。
    if "other_fee" in best:
        for col_idx, norm in enumerate(norm_headers):
            if col_idx in used or _alias_index("other_fee", norm) is None:
                continue
            if sum(1 for item in items if item.field == "other_fee") >= OTHER_FEE_MAX_COLUMNS:
                break
            items.append(
                MappingItem(
                    column_key=keys[col_idx],
                    field="other_fee",
                    options=alias_options("other_fee", normalize_header(headers[col_idx])),
                )
            )
            used.add(col_idx)
    unresolved = [keys[idx] for idx in range(len(headers)) if idx not in used]
    return items, unresolved


__all__ = [
    "DERIVED_TYPE_PREFIX",
    "TYPE_KEY_MAX_LEN",
    "MappingItem",
    "MappingPlan",
    "column_key",
    "column_keys",
    "derive_type_key",
    "header_signature",
    "merge_fee_units",
    "normalize_header_v2",
    "normalize_type_key",
    "plan_from_legacy_mapping",
    "suggest_items",
    "validate_plan",
]
