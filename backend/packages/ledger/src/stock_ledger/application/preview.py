"""导入第一阶段：上传预览（映射 → 类型 → 就绪三段式）。

硬断言：预览**不写任何 ledger.trade 行**。v2 流程：
1. `mapping`：识别表头、券商画像、给出映射建议（或复用模板）；
2. `types`：解析 dry-run 后按 `type_key` 聚合，未确认类型阻塞提交；
3. `ready`：映射与类型都齐备，可进入 commit。

legacy 兼容：调用方显式传 `mapping` 时保持旧行为（stage=`legacy`），旧前端/脚本不受影响。
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
from stock_contracts.enums import QUARANTINE_REASON_LABELS, WarningCode
from stock_contracts.errors import ApiError, ErrorCode, FieldError, NotFoundError
from stock_core.values import Money, Price, format_quantity
from stock_platform.config import Settings, get_settings
from stock_platform.ports import ObjectStore

from stock_ledger.application.chain import apply_multi_chain_plan, plan_chain_by_account
from stock_ledger.application.classify import load_rules
from stock_ledger.application.import_analysis import (
    TypeGroup,
    aggregate_types,
    apply_type_resolutions,
    completeness_issues,
    estimate_counts,
    public_catalog,
    public_categories,
    resolutions_from_types,
    resolve_broker,
    stage_for,
    suggestions_for_headers,
    unresolved_types,
    verify_fee_merge,
)
from stock_ledger.models import ImportPreview
from stock_ledger.parsers.huatai import (
    ParsedBill,
    ParsedRow,
    QuarantineCandidate,
    parse_bill,
    parse_bill_with_plan,
)
from stock_ledger.parsers.mapping import MappingPlan, validate_plan
from stock_ledger.parsers.profiles import detect_broker
from stock_ledger.parsers.table import inspect_table
from stock_ledger.repositories.import_mapping import (
    ImportBillTypeRepository,
    ImportColumnRuleRepository,
    ImportTemplateRepository,
    seed_import_column_rules,
)
from stock_ledger.repositories.imports import ImportBatchRepository, ImportPreviewRepository
from stock_ledger.repositories.trades import TradeRepository

SAMPLE_LIMIT = 10


class LocalObjectStore:
    """本地文件对象存储（实现 ObjectStore 端口）。"""

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
    quarantined_count: int
    sample: list[dict[str, Any]]
    expires_at: datetime
    columns_detail: list[dict[str, Any]] = field(default_factory=list)
    estimated_inserted: int = 0
    estimated_skipped: int = 0
    estimated_available: bool = True
    cash_balance_available: bool = False
    # ── v2 增量字段（legacy 预览保持默认值）────────────────────
    stage: str = "legacy"
    broker: str = "huatai"
    broker_detection: dict[str, Any] = field(default_factory=dict)
    signature: str | None = None
    header_row_index: int | None = None
    mapping_items: list[dict[str, Any]] = field(default_factory=list)
    ignored_columns: list[str] = field(default_factory=list)
    mapping_fingerprint: str | None = None
    mapping_version: int = 0
    template_id: str | None = None
    type_resolutions: list[dict[str, Any]] = field(default_factory=list)
    #: 解析期隔离行明细（行号/原因/日期/摘要…）；提交前弹窗提醒用。
    quarantine_rows: list[dict[str, Any]] = field(default_factory=list)
    blocking: list[dict[str, Any]] = field(default_factory=list)
    verification: dict[str, Any] = field(default_factory=dict)
    discarded_count: int = 0
    catalog: list[dict[str, object]] = field(default_factory=list)
    categories: list[dict[str, str]] = field(default_factory=list)
    system_categories: list[str] = field(default_factory=list)
    template: dict[str, Any] | None = None

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
            "estimated_available": self.estimated_available,
            "cash_balance_available": self.cash_balance_available,
            "stage": self.stage,
            "broker": self.broker,
            "broker_detection": self.broker_detection,
            "signature": self.signature,
            "header_row_index": self.header_row_index,
            "mapping_items": self.mapping_items,
            "ignored_columns": self.ignored_columns,
            "mapping_fingerprint": self.mapping_fingerprint,
            "mapping_version": self.mapping_version,
            "template_id": self.template_id,
            "type_resolutions": self.type_resolutions,
            "quarantine_rows": self.quarantine_rows,
            "blocking": self.blocking,
            "verification": self.verification,
            "discarded_count": self.discarded_count,
            "catalog": self.catalog,
            "categories": self.categories,
            "system_categories": self.system_categories,
            "template": self.template,
        }


def row_to_sample(row: ParsedRow) -> dict[str, Any]:
    return {
        "line_no": row.line_no,
        "trade_date": row.trade_date.isoformat(),
        "security_code": row.security_code,
        "security_name": row.security_name,
        "side": row.side,
        "quantity": format_quantity(row.quantity_units),
        "price": str(Price(row.price_units)),
        "amount": str(Money(row.amount_units)),
        "fees": str(Money(row.commission_units + row.stamp_tax_units + row.other_fee_units)),
        "cash_balance": (
            str(Money(row.cash_balance_units)) if row.cash_balance_units is not None else None
        ),
        "summary": row.summary,
        "occurrence_idx": row.occurrence_idx,
    }


#: 隔离明细展示的定位字段（canonical 字段名 → 输出键），值保持账单原文。
_QUARANTINE_LOCATOR_FIELDS: tuple[tuple[str, str], ...] = (
    ("trade_date", "date"),
    ("security_code", "security_code"),
    ("security_name", "security_name"),
    ("summary", "summary"),
    ("quantity", "quantity"),
    ("price", "price"),
    ("amount", "amount"),
    ("cash_balance", "cash_balance"),
)


def quarantine_rows_detail(
    quarantined: list[QuarantineCandidate],
    column_fields: dict[str, str],
) -> list[dict[str, Any]]:
    """隔离行 → 明细（行号、原因、日期/摘要等定位字段）；只做展示，不参与提交校验。"""
    rows: list[dict[str, Any]] = []
    locator_fields = {field for field, _ in _QUARANTINE_LOCATOR_FIELDS}
    for item in quarantined:
        values: dict[str, str] = {}
        for header, value in item.raw.items():
            # raw 以「原始表头 → 原文」为主；链路类隔离会额外注入 canonical 键（如 summary）。
            field = column_fields.get(header) or (header if header in locator_fields else None)
            if field and field not in values and value:
                values[field] = str(value)
        row: dict[str, Any] = {
            "line_no": item.line_no,
            "reason_code": item.reason_code,
            "reason": QUARANTINE_REASON_LABELS.get(item.reason_code, item.reason_code),
            "suggested_rule_id": item.suggested_rule_id,
        }
        for field, key in _QUARANTINE_LOCATOR_FIELDS:
            row[key] = values.get(field)
        rows.append(row)
    return rows


@dataclass(slots=True)
class _Analysis:
    bill: ParsedBill | None
    groups: dict[str, TypeGroup]
    blocking: list[dict[str, Any]]
    verification: dict[str, Any]
    extra_warnings: list[str]
    estimates: dict[str, int]
    stage: str
    quarantine_rows: list[dict[str, Any]] = field(default_factory=list)


async def _analyze(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    data: bytes,
    file_name: str,
    inspection: Any,
    plan: MappingPlan,
    broker: str,
    date_format: str | None,
    clock: Clock,
) -> _Analysis:
    issues = completeness_issues(plan)
    if issues:
        return _Analysis(None, {}, issues, {}, [], {}, "mapping")

    rules = await load_rules(session, user_id)
    try:
        bill = parse_bill_with_plan(
            data,
            file_name,
            plan=plan,
            date_format=date_format or None,
            clock=clock,
            broker=broker,
        )
    except ApiError as exc:
        if exc.code in {
            ErrorCode.IMPORT_PROFILE_REQUIRED,
            ErrorCode.IMPORT_COLUMNS_UNRESOLVED,
            ErrorCode.IMPORT_MAPPING_INVALID,
        }:
            return _Analysis(
                None,
                {},
                [{"field": "mapping", "code": exc.code.value, "message": str(exc.detail)}],
                {},
                [],
                {},
                "mapping",
            )
        raise
    bill.warnings = [warning for warning in bill.warnings if warning != WarningCode.UNKNOWN_SUMMARY]
    # 先做对账链规划：只把与库内账单链接得上的行送进类型聚合/计数/确认单。
    chain = await plan_chain_by_account(session, user_id=user_id, rows=bill.rows)
    apply_multi_chain_plan(bill, chain)

    verification, fee_warnings = verify_fee_merge(inspection, plan, bill)
    from stock_ledger.application.import_analysis import row_type_key

    keys = sorted({row_type_key(row)[0] for row in bill.rows})
    known = await ImportBillTypeRepository(session).by_keys(user_id, keys)
    groups = aggregate_types(bill, known_types=known, rules=rules)
    existing_keys = await TradeRepository(session).existing_dedupe_keys(
        user_id, [row.dedupe_key for row in bill.rows]
    )
    estimates = estimate_counts(bill, groups, existing_keys=existing_keys)
    return _Analysis(
        bill,
        groups,
        [],
        verification,
        fee_warnings,
        estimates,
        stage_for([], groups, parse_failed=False),
        quarantine_rows=quarantine_rows_detail(bill.quarantined, _plan_columns(inspection, plan)),
    )


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
    broker: str = "auto",
    reuse_template: bool = False,
) -> ImportPreviewDTO:
    """解析并落预览；v2 默认走映射/类型两段式，显式 `mapping` 保持 legacy。"""
    sha256 = hashlib.sha256(data).hexdigest()
    object_key = f"previews/{user_id}/{sha256}/{file_name}"
    await object_store.put_bytes(object_key, data)
    expires_at = clock.now() + timedelta(hours=IMPORT_PREVIEW_TTL_HOURS)

    if mapping is not None:
        bill = parse_bill(data, file_name, mapping=mapping, date_format=date_format, clock=clock)
        chain = await plan_chain_by_account(session, user_id=user_id, rows=bill.rows)
        apply_multi_chain_plan(bill, chain)
        rules = await load_rules(session, user_id)
        existing_keys = await TradeRepository(session).existing_dedupe_keys(
            user_id, [row.dedupe_key for row in bill.rows]
        )
        unknown_rows = [row for row in bill.rows if _legacy_unknown(row, rules)]
        warnings = [warning.value for warning in bill.warnings]
        assumptions: dict[str, Any] = dict(bill.assumptions)
        if unknown_rows:
            warnings.append(WarningCode.UNKNOWN_SUMMARY.value)
            assumptions["unknown_summary_rows"] = [
                {"line_no": row.line_no, "summary": row.summary} for row in unknown_rows
            ]
        inserted = 0
        skipped = 0
        seen: set[str] = set()
        for row in bill.rows:
            if row.dedupe_key in existing_keys or row.dedupe_key in seen:
                skipped += 1
                continue
            seen.add(row.dedupe_key)
            if _legacy_unknown(row, rules):
                continue
            inserted += 1
        quarantine_rows = quarantine_rows_detail(bill.quarantined, bill.columns)
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
            quarantined_count=len(bill.quarantined) + len(unknown_rows),
            unresolved_columns=bill.unresolved,
            expires_at=expires_at,
            broker="huatai",
            stage="legacy",
            mapping_items=[],
            mapping_version=0,
            verification={},
            quarantine_rows=quarantine_rows,
        )
        return _dto_from_preview(
            preview,
            bill=bill,
            columns_detail=_legacy_columns_detail(bill),
            estimated_inserted=inserted,
            estimated_skipped=skipped,
            warnings=warnings,
            assumptions=assumptions,
            quarantine_rows=quarantine_rows,
        )

    column_rules = await _load_column_rules(session, user_id)
    inspection = inspect_table(data, file_name, rules=column_rules)
    detection = detect_broker(inspection.headers, column_rules)
    resolved_broker = resolve_broker(broker, detection.code)
    templates = ImportTemplateRepository(session)
    plan: MappingPlan | None = None
    template_id: str | None = None
    template_payload: dict[str, Any] | None = None
    if reuse_template:
        template = await templates.by_signature(user_id, inspection.signature)
        if template is not None:
            plan = MappingPlan.from_dict(
                {
                    "header_row_index": inspection.header_row_index,
                    "items": template.items,
                    "ignored": template.ignored,
                }
            )
            template_id = template.id
            template_payload = _template_dict(template)
    if plan is None:
        from stock_ledger.parsers.mapping import suggest_items

        items, _unresolved = suggest_items(inspection.headers)
        suggested = {item.column_key for item in items}
        plan = MappingPlan(
            header_row_index=inspection.header_row_index,
            items=tuple(items),
            ignored=tuple(
                key
                for key, header in zip(inspection.keys, inspection.headers, strict=False)
                if key not in suggested and bool(header)
            ),
        )

    try:
        analysis = await _analyze(
            session,
            user_id=user_id,
            data=data,
            file_name=file_name,
            inspection=inspection,
            plan=plan,
            broker=resolved_broker,
            date_format=date_format,
            clock=clock,
        )
    except ApiError as exc:
        # 自动建议可能把多账户账单的其他费全部合并；让用户进入映射步骤修正，
        # 而不是在展示建议前就 422。API-098 保存映射时仍执行同样的硬校验。
        if template_id is not None or exc.code not in {
            ErrorCode.IMPORT_FEE_MERGE_INVALID,
            ErrorCode.IMPORT_FEE_MERGE_VERIFY_FAILED,
        }:
            raise
        analysis = _Analysis(
            None,
            {},
            [{"field": "mapping", "code": exc.code.value, "message": str(exc.detail)}],
            {},
            [],
            {},
            "mapping",
        )
    warnings = _v2_warnings(analysis)
    if await _has_reimport_conflict(session, user_id, sha256, plan.fingerprint()):
        warnings.append(WarningCode.MAPPING_CHANGED_REIMPORT.value)
    assumptions = _v2_assumptions(analysis, detection)
    preview = await ImportPreviewRepository(session).create(
        user_id,
        file_name=file_name,
        file_sha256=sha256,
        file_bytes=len(data),
        object_key=object_key,
        columns=_plan_columns(inspection, plan),
        mapping={},
        date_format=analysis.bill.date_format if analysis.bill else date_format,
        warnings=warnings,
        sample=[row_to_sample(row) for row in (analysis.bill.rows if analysis.bill else [])][
            :SAMPLE_LIMIT
        ],
        row_count=inspection.row_count,
        parsed_count=len(analysis.bill.rows) if analysis.bill else 0,
        quarantined_count=analysis.estimates.get("quarantined", 0),
        unresolved_columns=_unresolved_headers(inspection, plan),
        expires_at=expires_at,
        broker=resolved_broker,
        signature=inspection.signature,
        header_row_index=inspection.header_row_index,
        mapping_fingerprint=plan.fingerprint(),
        stage=analysis.stage,
        mapping_items=plan.as_items(),
        ignored_columns=list(plan.ignored),
        mapping_version=1,
        template_id=template_id,
        verification=analysis.verification,
        discarded_count=analysis.estimates.get("discarded", 0),
        type_resolutions=resolutions_from_types(analysis.groups),
        quarantine_rows=analysis.quarantine_rows,
    )
    dto = _dto_for_analysis(preview, analysis=analysis, inspection=inspection, detection=detection)
    dto.template = template_payload
    return dto


async def save_mapping(
    session: AsyncSession,
    *,
    clock: Clock,
    user_id: uuid.UUID,
    preview_id: str,
    plan: MappingPlan,
    broker: str | None,
    date_format: str | None,
    ack_unmapped: bool,
    expected_mapping_version: int | None,
    object_store: ObjectStore,
) -> ImportPreviewDTO:
    """API-098：保存映射并重跑类型分析（映射变更使旧类型确认失效）。"""
    repo = ImportPreviewRepository(session)
    preview = await repo.lock_by_id(user_id, preview_id, now=clock.now())
    if preview.stage not in {"mapping", "types", "ready"}:
        raise ApiError(ErrorCode.IMPORT_STAGE_CONFLICT, detail="当前预览阶段不允许修改映射")
    await _ensure_preview_editable(session, user_id, preview)
    if expected_mapping_version is not None and expected_mapping_version != preview.mapping_version:
        raise ApiError(ErrorCode.VERSION_CONFLICT, detail="预览已被修改，请刷新后重试")
    validate_plan(plan)

    data = await object_store.get_bytes(preview.object_key)
    column_rules = await _load_column_rules(session, user_id)
    inspection = inspect_table(data, preview.file_name, rules=column_rules)
    resolved_broker = broker or preview.broker
    if broker and broker != "auto":
        resolved_broker = broker
    bill_plan = MappingPlan(
        header_row_index=inspection.header_row_index,
        items=plan.items,
        ignored=plan.ignored,
    )
    unresolved = _unresolved_headers(inspection, bill_plan)
    if unresolved and not ack_unmapped:
        raise ApiError(
            ErrorCode.IMPORT_UNMAPPED_COLUMNS_UNCONFIRMED,
            detail=f"还有 {len(unresolved)} 列未映射，请确认忽略后再继续",
        )
    analysis = await _analyze(
        session,
        user_id=user_id,
        data=data,
        file_name=preview.file_name,
        inspection=inspection,
        plan=bill_plan,
        broker=resolved_broker,
        date_format=date_format,
        clock=clock,
    )
    if analysis.blocking:
        issue_codes = {str(issue.get("code")) for issue in analysis.blocking}
        code = next(
            (
                candidate
                for candidate in (
                    ErrorCode.IMPORT_PROFILE_REQUIRED,
                    ErrorCode.IMPORT_MAPPING_INVALID,
                    ErrorCode.IMPORT_CLASSIFICATION_SOURCE_MISSING,
                )
                if candidate.value in issue_codes
            ),
            ErrorCode.IMPORT_COLUMNS_UNRESOLVED,
        )
        raise ApiError(
            code,
            errors=[
                FieldError(
                    str(issue.get("field") or "mapping"),
                    str(issue.get("code") or "MAPPING_REQUIRED"),
                    str(issue.get("message") or "映射不完整"),
                )
                for issue in analysis.blocking
            ],
            detail="列对应尚未满足导入要求",
        )
    preview.broker = resolved_broker
    preview.signature = inspection.signature
    preview.header_row_index = inspection.header_row_index
    preview.mapping_fingerprint = bill_plan.fingerprint()
    preview.mapping_items = bill_plan.as_items()
    preview.ignored_columns = list(bill_plan.ignored)
    preview.mapping_version += 1
    preview.mapping_confirmed_at = clock.now()
    preview.stage = analysis.stage
    preview.columns = _plan_columns(inspection, bill_plan)
    preview.mapping = {}
    preview.verification = analysis.verification
    _keep_user_type_choices(preview, analysis)
    preview.type_resolutions = resolutions_from_types(analysis.groups)
    preview.types_confirmed_at = None
    preview.discarded_count = analysis.estimates.get("discarded", 0)
    _apply_analysis_counts(preview, analysis)
    warnings = _v2_warnings(analysis)
    if await _has_reimport_conflict(session, user_id, preview.file_sha256, bill_plan.fingerprint()):
        warnings.append(WarningCode.MAPPING_CHANGED_REIMPORT.value)
    preview.warnings = warnings
    if analysis.bill is not None and date_format is not None:
        preview.date_format = date_format
    elif analysis.bill is not None:
        preview.date_format = analysis.bill.date_format
    await session.flush()
    detection = detect_broker(inspection.headers, column_rules)
    dto = _dto_for_analysis(preview, analysis=analysis, inspection=inspection, detection=detection)
    dto.template = await _load_template_payload(session, user_id, preview)
    return dto


async def save_types(
    session: AsyncSession,
    *,
    clock: Clock,
    user_id: uuid.UUID,
    preview_id: str,
    resolutions: list[dict[str, Any]],
    expected_mapping_version: int | None,
    object_store: ObjectStore,
) -> ImportPreviewDTO:
    """API-099：保存类型确认；全部确认后进入 `ready`。"""
    repo = ImportPreviewRepository(session)
    preview = await repo.lock_by_id(user_id, preview_id, now=clock.now())
    if preview.stage not in {"types", "ready"}:
        raise ApiError(ErrorCode.IMPORT_STAGE_CONFLICT, detail="当前预览阶段不允许确认类型")
    await _ensure_preview_editable(session, user_id, preview)
    if expected_mapping_version is not None and expected_mapping_version != preview.mapping_version:
        raise ApiError(ErrorCode.VERSION_CONFLICT, detail="预览已被修改，请刷新后重试")

    data = await object_store.get_bytes(preview.object_key)
    column_rules = await _load_column_rules(session, user_id)
    inspection = inspect_table(data, preview.file_name, rules=column_rules)
    plan = MappingPlan(
        header_row_index=preview.header_row_index or inspection.header_row_index,
        items=_items(list(preview.mapping_items or [])),
        ignored=tuple(preview.ignored_columns),
    )
    analysis = await _analyze(
        session,
        user_id=user_id,
        data=data,
        file_name=preview.file_name,
        inspection=inspection,
        plan=plan,
        broker=preview.broker,
        date_format=preview.date_format,
        clock=clock,
    )
    groups = apply_type_resolutions(analysis.groups, resolutions)
    remaining = unresolved_types(groups)
    stage = "types" if remaining else "ready"
    estimates = (
        estimate_counts(
            analysis.bill,
            groups,
            existing_keys=await TradeRepository(session).existing_dedupe_keys(
                user_id, [row.dedupe_key for row in analysis.bill.rows]
            ),
        )
        if analysis.bill is not None
        else analysis.estimates
    )
    analysis.groups = groups
    analysis.estimates = estimates
    analysis.stage = stage
    preview.type_resolutions = resolutions_from_types(groups)
    preview.stage = stage
    preview.types_confirmed_at = clock.now() if stage == "ready" else None
    preview.discarded_count = estimates.get("discarded", 0)
    _apply_analysis_counts(preview, analysis)
    await session.flush()
    dto = _dto_for_analysis(
        preview,
        analysis=analysis,
        inspection=inspection,
        detection=detect_broker(inspection.headers, column_rules),
    )
    dto.template = await _load_template_payload(session, user_id, preview)
    return dto


def preview_to_dto(preview: ImportPreview) -> ImportPreviewDTO:
    catalog = public_catalog()
    categories, system = public_categories()
    blocking: list[dict[str, Any]] = []
    if preview.stage == "mapping":
        blocking = completeness_issues(
            MappingPlan(
                header_row_index=preview.header_row_index or 0,
                items=_items(list(preview.mapping_items or [])),
                ignored=tuple(preview.ignored_columns or []),
            )
        )
    return ImportPreviewDTO(
        preview_id=preview.id,
        file_name=preview.file_name,
        file_sha256=preview.file_sha256,
        file_bytes=preview.file_bytes,
        object_key=preview.object_key,
        columns=dict(preview.columns or {}),
        mapping=dict(preview.mapping or {}),
        date_format=preview.date_format,
        warnings=list(preview.warnings or []),
        assumptions={},
        unresolved_columns=list(preview.unresolved_columns or []),
        row_count=preview.row_count,
        parsed_count=preview.parsed_count,
        quarantined_count=preview.quarantined_count,
        sample=list(preview.sample or []),
        expires_at=preview.expires_at,
        columns_detail=[
            {
                "index": idx,
                "key": key,
                "name": name,
                "sample": "",
                "samples": [],
                "suggested_field": None,
                "suggested_options": {},
                "level": None,
                "confidence": 0.0,
            }
            for idx, (name, key) in enumerate((preview.columns or {}).items())
        ],
        estimated_inserted=preview.parsed_count - preview.quarantined_count,
        estimated_skipped=0,
        estimated_available=preview.stage != "mapping",
        cash_balance_available=False,
        stage=preview.stage,
        broker=preview.broker,
        signature=preview.signature,
        header_row_index=preview.header_row_index,
        mapping_items=list(preview.mapping_items or []),
        ignored_columns=list(preview.ignored_columns or []),
        mapping_fingerprint=preview.mapping_fingerprint,
        mapping_version=preview.mapping_version,
        template_id=preview.template_id,
        type_resolutions=list(preview.type_resolutions or []),
        quarantine_rows=list(preview.quarantine_rows or []),
        blocking=blocking,
        verification=dict(preview.verification or {}),
        discarded_count=preview.discarded_count,
        catalog=catalog,
        categories=categories,
        system_categories=system,
    )


async def _ensure_preview_editable(
    session: AsyncSession, user_id: uuid.UUID, preview: ImportPreview
) -> None:
    batch = await ImportBatchRepository(session).by_preview(user_id, preview.id)
    if (
        batch is not None
        and batch.revoked_at is None
        and batch.status in {"pending", "running", "succeeded"}
    ):
        raise ApiError(
            ErrorCode.IMPORT_STAGE_CONFLICT,
            detail="预览已提交，不能再修改列对应或账单类型",
        )


async def require_preview(
    session: AsyncSession, user_id: uuid.UUID, preview_id: str
) -> ImportPreview:
    preview = await ImportPreviewRepository(session).by_id(user_id, preview_id)
    if preview is None:
        raise NotFoundError(detail=f"预览 {preview_id} 不存在")
    return preview


async def _load_column_rules(session: AsyncSession, user_id: uuid.UUID) -> list[Any]:
    # 自愈 seed：只插缺、不覆盖管理员改动（设计 §3.5）。
    await seed_import_column_rules(session)
    return await ImportColumnRuleRepository(session).list_effective(user_id)


def _template_dict(template: Any) -> dict[str, Any]:
    return {
        "template_id": template.id,
        "broker": template.broker,
        "signature": template.signature,
        "header_count": template.header_count,
        "date_format": template.date_format,
        "items": list(template.items or []),
        "ignored": list(template.ignored or []),
        "origin_batch_id": template.origin_batch_id,
        "last_batch_id": template.last_batch_id,
        "created_at": template.created_at.isoformat() if template.created_at else None,
        "updated_at": template.updated_at.isoformat() if template.updated_at else None,
    }


async def load_template_payload(
    session: AsyncSession, user_id: uuid.UUID, preview: ImportPreview
) -> dict[str, Any] | None:
    """预览关联的模板（API-106 刷新时回填「已复用模板」提示）。"""
    if not preview.template_id:
        return None
    template = await ImportTemplateRepository(session).by_id(user_id, preview.template_id)
    return _template_dict(template) if template is not None else None


async def _load_template_payload(
    session: AsyncSession, user_id: uuid.UUID, preview: ImportPreview
) -> dict[str, Any] | None:
    return await load_template_payload(session, user_id, preview)


async def _has_reimport_conflict(
    session: AsyncSession,
    user_id: uuid.UUID,
    file_sha256: str,
    mapping_fingerprint: str | None,
) -> bool:
    """同一文件曾用不同映射成功导入过 → 提示改用区间替换或显式确认重导。"""
    if not mapping_fingerprint:
        return False
    return await ImportBatchRepository(session).has_mapping_conflict(
        user_id, file_sha256, mapping_fingerprint
    )


def _legacy_unknown(row: ParsedRow, rules: list[Any]) -> bool:
    from stock_ledger.application.classify import match_rule

    return match_rule(rules, row.summary) is None


def _plan_columns(inspection: Any, plan: MappingPlan) -> dict[str, str]:
    by_key = {item.column_key: item.field for item in plan.items}
    return {
        header: by_key[key]
        for key, header in zip(inspection.keys, inspection.headers, strict=False)
        if key in by_key
    }


def _unresolved_headers(inspection: Any, plan: MappingPlan) -> list[str]:
    mapped = {item.column_key for item in plan.items} | set(plan.ignored)
    return [
        header
        for key, header in zip(inspection.keys, inspection.headers, strict=False)
        if key not in mapped and (bool(header) or bool(inspection.samples.get(key)))
    ]


def _v2_warnings(analysis: _Analysis) -> list[str]:
    warnings: list[str] = []
    if analysis.bill is not None:
        warnings.extend(warning.value for warning in analysis.bill.warnings)
    warnings.extend(analysis.extra_warnings)
    if analysis.estimates.get("unknown", 0) > 0:
        warnings.append(WarningCode.UNKNOWN_SUMMARY.value)
    return warnings


def _v2_assumptions(analysis: _Analysis, detection: Any) -> dict[str, Any]:
    assumptions: dict[str, Any] = dict(analysis.bill.assumptions) if analysis.bill else {}
    assumptions["broker_detection"] = {
        "code": detection.code,
        "confidence": detection.confidence,
        "ambiguous": detection.ambiguous,
        "evidence": detection.evidence,
        "candidates": detection.candidates,
    }
    return assumptions


def _apply_analysis_counts(preview: ImportPreview, analysis: _Analysis) -> None:
    if analysis.bill is None:
        preview.sample = []
        preview.parsed_count = 0
        preview.quarantined_count = 0
        preview.quarantine_rows = []
        return
    preview.sample = [row_to_sample(row) for row in analysis.bill.rows[:SAMPLE_LIMIT]]
    preview.parsed_count = len(analysis.bill.rows)
    preview.quarantined_count = analysis.estimates.get("quarantined", 0)
    preview.quarantine_rows = list(analysis.quarantine_rows)


def _keep_user_type_choices(preview: ImportPreview, analysis: _Analysis) -> None:
    """退回改映射时保留用户此前手动选的分类/废弃（C），但类型仍需整步重新确认（E）。"""
    previous = {
        str(item.get("type_key")): item
        for item in (preview.type_resolutions or [])
        if item.get("source") == "user"
    }
    for key, group in analysis.groups.items():
        item = previous.get(key)
        if item is None:
            continue
        if bool(item.get("discard")):
            group.discard = True
            group.category = None
        else:
            group.discard = False
            group.category = item.get("category")


def _dto_for_analysis(
    preview: ImportPreview,
    *,
    analysis: _Analysis,
    inspection: Any,
    detection: Any,
) -> ImportPreviewDTO:
    catalog = public_catalog()
    categories, system = public_categories()
    bill = analysis.bill
    return ImportPreviewDTO(
        preview_id=preview.id,
        file_name=preview.file_name,
        file_sha256=preview.file_sha256,
        file_bytes=preview.file_bytes,
        object_key=preview.object_key,
        columns=dict(preview.columns or {}),
        mapping=dict(preview.mapping or {}),
        date_format=preview.date_format,
        # 以落库 warnings 为准（含 MAPPING_CHANGED_REIMPORT 等跨阶段提示）。
        warnings=list(preview.warnings or []) or _v2_warnings(analysis),
        assumptions=_v2_assumptions(analysis, detection),
        unresolved_columns=_unresolved_headers(
            inspection,
            MappingPlan(
                header_row_index=preview.header_row_index or inspection.header_row_index,
                items=_items(list(preview.mapping_items or [])),
                ignored=tuple(preview.ignored_columns or []),
            ),
        ),
        row_count=preview.row_count,
        parsed_count=preview.parsed_count,
        quarantined_count=preview.quarantined_count,
        sample=[row_to_sample(row) for row in (bill.rows if bill else [])][:SAMPLE_LIMIT],
        expires_at=preview.expires_at,
        columns_detail=suggestions_for_headers(inspection),
        estimated_inserted=analysis.estimates.get("inserted", 0),
        estimated_skipped=analysis.estimates.get("skipped", 0),
        cash_balance_available=bool(bill and any(row.cash_balance_available for row in bill.rows)),
        stage=preview.stage,
        broker=preview.broker,
        broker_detection={
            "code": detection.code,
            "confidence": detection.confidence,
            "ambiguous": detection.ambiguous,
            "evidence": detection.evidence,
            "candidates": detection.candidates,
        },
        signature=preview.signature,
        header_row_index=preview.header_row_index,
        mapping_items=list(preview.mapping_items or []),
        ignored_columns=list(preview.ignored_columns or []),
        mapping_fingerprint=preview.mapping_fingerprint,
        mapping_version=preview.mapping_version,
        template_id=preview.template_id,
        type_resolutions=list(preview.type_resolutions or []),
        quarantine_rows=list(analysis.quarantine_rows),
        blocking=analysis.blocking,
        verification=dict(preview.verification or {}),
        discarded_count=preview.discarded_count,
        catalog=catalog,
        categories=categories,
        system_categories=system,
    )


def _items(raw: list[dict[str, Any]]) -> tuple[Any, ...]:
    from stock_ledger.parsers.mapping import MappingItem

    return tuple(MappingItem.from_dict(item) for item in raw)


def _dto_from_preview(
    preview: ImportPreview,
    *,
    bill: ParsedBill,
    columns_detail: list[dict[str, Any]],
    estimated_inserted: int,
    estimated_skipped: int,
    warnings: list[str],
    assumptions: dict[str, Any],
    quarantine_rows: list[dict[str, Any]],
) -> ImportPreviewDTO:
    return ImportPreviewDTO(
        preview_id=preview.id,
        file_name=preview.file_name,
        file_sha256=preview.file_sha256,
        file_bytes=preview.file_bytes,
        object_key=preview.object_key,
        columns=bill.columns,
        mapping=dict(preview.mapping or {}),
        date_format=bill.date_format,
        warnings=warnings,
        assumptions=assumptions,
        unresolved_columns=bill.unresolved,
        row_count=bill.row_count,
        parsed_count=len(bill.rows),
        quarantined_count=preview.quarantined_count,
        sample=[row_to_sample(row) for row in bill.rows[:SAMPLE_LIMIT]],
        expires_at=preview.expires_at,
        columns_detail=columns_detail,
        estimated_inserted=estimated_inserted,
        estimated_skipped=estimated_skipped,
        cash_balance_available=any(row.cash_balance_available for row in bill.rows),
        stage="legacy",
        broker="huatai",
        quarantine_rows=list(quarantine_rows),
    )


def _legacy_columns_detail(bill: ParsedBill) -> list[dict[str, Any]]:
    first_raw = bill.rows[0].raw if bill.rows else {}
    all_headers = list(first_raw.keys())

    def first_non_empty(header: str) -> str:
        for row in bill.rows:
            value = row.raw.get(header, "")
            if value:
                return value
        return first_raw.get(header, "")

    return [
        {
            "index": index,
            "name": header,
            "sample": first_non_empty(header),
            "suggested_field": bill.columns.get(header),
            "confidence": 1.0,
        }
        for index, header in enumerate(all_headers)
    ]


__all__ = [
    "SAMPLE_LIMIT",
    "ImportPreviewDTO",
    "LocalObjectStore",
    "create_preview",
    "default_object_store",
    "load_template_payload",
    "preview_to_dto",
    "require_preview",
    "row_to_sample",
    "save_mapping",
    "save_types",
]
