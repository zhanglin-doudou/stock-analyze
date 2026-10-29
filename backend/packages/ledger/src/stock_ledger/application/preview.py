"""导入第一阶段：上传预览（预检）。

硬断言：预览**不写任何 ledger.trade 行**。这里只把原始文件落到对象存储、把解析出的
列映射/预检计数/警告/假设/样例（≤10 行）落 `import_preview`，等用户在“需求确认单”上
点确认才进第二阶段（commit_batch）。这样半截脏数据永远进不了统计口径。

G3：预览阶段要做**分类 dry-run**（默认+用户摘要规则），未知摘要计入
`estimated.quarantined` 并给 `UNKNOWN_SUMMARY` 警告（行号/摘要明细在
`assumptions.unknown_summary_rows`），保证「预览的隔离数 == commit 的实际隔离数」。
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession
from stock_contracts.clock import Clock
from stock_contracts.constants import IMPORT_PREVIEW_TTL_HOURS
from stock_contracts.enums import WarningCode
from stock_core.values import Money, Price, Qty
from stock_platform.config import Settings, get_settings
from stock_platform.ports import ObjectStore

from stock_ledger.application.classify import load_rules, match_rule
from stock_ledger.models import SummaryRule
from stock_ledger.parsers.huatai import ParsedRow, parse_bill
from stock_ledger.repositories.imports import ImportPreviewRepository
from stock_ledger.repositories.trades import TradeRepository

SAMPLE_LIMIT = 10


class LocalObjectStore:
    """本地文件对象存储（实现 ObjectStore 端口）。

    预览阶段原文件必须留档：提交阶段要重新解析，撤销后也便于追责/复现。
    生产可替换为 S3 兼容实现，端口不变。
    """

    __slots__ = ("_base",)

    def __init__(self, base: Path) -> None:
        self._base = base

    def _path(self, key: str) -> Path:
        return self._base / key

    async def put_bytes(self, key: str, data: bytes) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    async def get_bytes(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    async def exists(self, key: str) -> bool:
        return self._path(key).exists()

    async def list_prefix(self, prefix: str) -> list[str]:
        root = self._path(prefix)
        if not root.exists():
            return []
        if root.is_file():
            return [prefix]
        return sorted(str(p.relative_to(self._base)) for p in root.rglob("*") if p.is_file())


def default_object_store() -> LocalObjectStore:
    """默认对象存储落点：`settings.data_dir/objects`（提交阶段与预览阶段共用）。"""
    return LocalObjectStore(get_settings().data_dir / "objects")


@dataclass(slots=True)
class ImportPreviewDTO:
    """预览返回体：需求确认单所需的全部字段。"""

    preview_id: str
    file_name: str
    file_sha256: str
    file_bytes: int
    object_key: str
    columns: dict[str, str]
    mapping: dict[str, str]
    date_format: str | None
    warnings: list[str]
    assumptions: dict[str, Any]
    unresolved_columns: list[str]
    row_count: int
    parsed_count: int
    # 预估隔离总数 = 解析隔离 + 分类未知摘要（与 commit 的 batch.quarantined_count 同口径）
    quarantined_count: int
    sample: list[dict[str, Any]]
    expires_at: datetime
    # 供 API 边界 `PreviewOut` 直接消费的扩展字段（不改变既有字段语义）
    columns_detail: list[dict[str, Any]] = field(default_factory=list)
    estimated_inserted: int = 0
    estimated_skipped: int = 0
    cash_balance_available: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "preview_id": self.preview_id,
            "file_name": self.file_name,
            "file_sha256": self.file_sha256,
            "file_bytes": self.file_bytes,
            "columns": self.columns,
            "mapping": self.mapping,
            "date_format": self.date_format,
            "warnings": self.warnings,
            "assumptions": self.assumptions,
            "unresolved_columns": self.unresolved_columns,
            "row_count": self.row_count,
            "parsed_count": self.parsed_count,
            "quarantined_count": self.quarantined_count,
            "sample": self.sample,
            "expires_at": self.expires_at.isoformat(),
            "columns_detail": self.columns_detail,
            "estimated_inserted": self.estimated_inserted,
            "estimated_skipped": self.estimated_skipped,
            "cash_balance_available": self.cash_balance_available,
        }


def row_to_sample(row: ParsedRow) -> dict[str, Any]:
    """明细行 → 预览样例（金额/价格/数量输出定点字符串，不暴露内部标度整数）。"""
    return {
        "line_no": row.line_no,
        "trade_date": row.trade_date.isoformat(),
        "security_code": row.security_code,
        "security_name": row.security_name,
        "side": row.side,
        "quantity": str(Qty(row.quantity_units)),
        "price": str(Price(row.price_units)),
        "amount": str(Money(row.amount_units)),
        "fees": str(Money(row.commission_units + row.stamp_tax_units + row.other_fee_units)),
        "cash_balance": (
            str(Money(row.cash_balance_units)) if row.cash_balance_units is not None else None
        ),
        "money_fund_balance": (
            str(Money(row.money_fund_balance_units))
            if row.money_fund_balance_units is not None
            else None
        ),
        "summary": row.summary,
        "occurrence_idx": row.occurrence_idx,
    }


async def create_preview(
    session: AsyncSession,
    *,
    clock: Clock,
    settings: Settings,
    user_id: uuid.UUID,
    data: bytes,
    file_name: str,
    mapping: dict[str, str] | None = None,
    date_format: str | None = None,
    object_store: ObjectStore,
) -> ImportPreviewDTO:
    """解析并落预览；不触碰 ledger.trade（只读 dry-run 分类，不写业务行）。"""
    bill = parse_bill(data, file_name, mapping=mapping, date_format=date_format, clock=clock)
    sha256 = hashlib.sha256(data).hexdigest()
    object_key = f"previews/{user_id}/{sha256}/{file_name}"
    await object_store.put_bytes(object_key, data)

    # 分类 dry-run：与 commit 完全同一套规则 + 同一套去重判定，保证隔离计数一致。
    rules = await load_rules(session, user_id)
    existing_keys = await TradeRepository(session).existing_dedupe_keys(
        user_id, [row.dedupe_key for row in bill.rows]
    )
    estimated_inserted, estimated_skipped, unknown_rows = _estimate_counts(
        bill.rows, existing_keys=existing_keys, rules=rules
    )

    warnings = [warning.value for warning in bill.warnings]
    assumptions: dict[str, Any] = dict(bill.assumptions)
    if unknown_rows:
        warnings.append(WarningCode.UNKNOWN_SUMMARY.value)
        assumptions["unknown_summary_rows"] = [
            {"line_no": row.line_no, "summary": row.summary, "side": row.side}
            for row in unknown_rows
        ]
    # 解析隔离（重复行/坏行）+ 分类未知摘要，与 commit 的 quarantine 条数同口径。
    estimated_quarantined = len(bill.quarantined) + len(unknown_rows)

    expires_at = clock.now() + timedelta(hours=IMPORT_PREVIEW_TTL_HOURS)
    preview = await ImportPreviewRepository(session).create(
        user_id,
        file_name=file_name,
        file_sha256=sha256,
        file_bytes=len(data),
        object_key=object_key,
        columns=bill.columns,
        mapping=mapping or {},
        date_format=bill.date_format,
        warnings=warnings,
        sample=[row_to_sample(row) for row in bill.rows[:SAMPLE_LIMIT]],
        row_count=bill.row_count,
        parsed_count=len(bill.rows),
        quarantined_count=estimated_quarantined,
        unresolved_columns=bill.unresolved,
        expires_at=expires_at,
    )

    # 表头明细：按表头顺序回填样例/建议字段，供 API 组 `PreviewColumn`。
    # 样例取所有行中第一个非空值（非交易行可能账号为空，不能只看第一行）。
    first_raw = bill.rows[0].raw if bill.rows else {}
    all_headers = list(first_raw.keys())

    def _first_non_empty(header: str) -> str:
        for row in bill.rows:
            value = row.raw.get(header, "")
            if value:
                return value
        return first_raw.get(header, "")

    columns_detail = [
        {
            "index": index,
            "name": header,
            "sample": _first_non_empty(header),
            "suggested_field": bill.columns.get(header),
            "confidence": 1.0,
        }
        for index, header in enumerate(all_headers)
    ]

    # 只读估算：命中已有去重键或文件内重复的行计入 skipped（不写库）；
    # 其余行做分类 dry-run，未命中规则的计入预估隔离。
    estimated_inserted, estimated_skipped, unknown_rows = _estimate_counts(
        bill.rows, existing_keys=existing_keys, rules=rules
    )

    return ImportPreviewDTO(
        preview_id=preview.id,
        file_name=preview.file_name,
        file_sha256=preview.file_sha256,
        file_bytes=preview.file_bytes,
        object_key=preview.object_key,
        columns=bill.columns,
        mapping=mapping or {},
        date_format=bill.date_format,
        warnings=warnings,
        assumptions=assumptions,
        unresolved_columns=bill.unresolved,
        row_count=bill.row_count,
        parsed_count=len(bill.rows),
        quarantined_count=estimated_quarantined,
        sample=[row_to_sample(row) for row in bill.rows[:SAMPLE_LIMIT]],
        expires_at=preview.expires_at,
        columns_detail=columns_detail,
        estimated_inserted=estimated_inserted,
        estimated_skipped=estimated_skipped,
        cash_balance_available=any(row.cash_balance_available for row in bill.rows),
    )


def _estimate_counts(
    rows: list[ParsedRow], *, existing_keys: set[str], rules: list[SummaryRule]
) -> tuple[int, int, list[ParsedRow]]:
    """逐行预估 `(inserted, skipped, unknown_rows)`。

    与 commit 的执行顺序保持一致：先 dedupe（已存在/文件内重复 → skipped），
    再分类（未命中 → 预估隔离），其余才计入 inserted。`skipped` 行不会被重复隔离。
    """
    seen: set[str] = set()
    inserted = 0
    skipped = 0
    unknown_rows: list[ParsedRow] = []
    for row in rows:
        if row.dedupe_key in existing_keys or row.dedupe_key in seen:
            skipped += 1
            continue
        seen.add(row.dedupe_key)
        if match_rule(rules, row.summary, row.side) is None:
            unknown_rows.append(row)
            continue
        inserted += 1
    return inserted, skipped, unknown_rows


__all__ = [
    "SAMPLE_LIMIT",
    "ImportPreviewDTO",
    "LocalObjectStore",
    "create_preview",
    "default_object_store",
    "row_to_sample",
]
