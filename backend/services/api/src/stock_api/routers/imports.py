"""导入端点（API-010~015）：预览 / 提交 / 列表 / 详情 / 隔离区 / 撤销。

两阶段导入：预览（API-010）只落 `import_preview`，**绝不写 ledger.trade**；提交（API-011）
同步创建 PENDING 批次 + 入队异步 job，由 worker 收养该批次并写明细；撤销（API-015）同步 200。
所有端点强制 `user_id` 过滤，跨账号一律 404；admin 不可触碰业务数据（§14.2）。
"""

from __future__ import annotations

import hashlib
import json
from typing import Annotated, Any

from fastapi import (
    APIRouter,
    Body,
    Depends,
    File,
    Form,
    Header,
    Query,
    Request,
    Response,
    UploadFile,
)
from fastapi.responses import JSONResponse
from stock_contracts.enums import ImportBatchStatus
from stock_contracts.errors import ApiError, ErrorCode, FieldError, NotFoundError
from stock_ledger.application.imports import revoke_batch_now
from stock_ledger.application.preview import ImportPreviewDTO, create_preview
from stock_ledger.repositories.imports import ImportBatchRepository, ImportPreviewRepository
from stock_ledger.repositories.quarantine import QuarantineRepository
from stock_platform.idempotency import IdempotencyStore
from stock_platform.tenant import TenantContext

from stock_api.deps import get_state, get_tenant
from stock_api.routing import StrictQueryRoute
from stock_api.schemas import (
    BatchCounts,
    BatchOut,
    BatchPage,
    ImportCommitIn,
    JobAcceptedOut,
    JobLinks,
    PreviewColumn,
    PreviewEstimated,
    PreviewFile,
    PreviewOut,
    PreviewWarning,
    QuarantineItem,
    QuarantinePage,
    QuarantineRaw,
    RevokeIn,
    RevokeOut,
)

router = APIRouter(tags=["imports"], route_class=StrictQueryRoute)

_ENDPOINT_COMMIT = "POST /imports"
_JOBS_PREFIX = "/api/v1/jobs"

# 隔离区原始列（原始表头 → canonical 字段）到 `QuarantineRaw` 字段的映射
_QUARANTINE_FIELDS: dict[str, str] = {
    "trade_date": "date",
    "security_code": "security_code",
    "summary": "summary",
    "quantity": "quantity",
    "price": "price",
    "amount": "amount",
    "cash_balance": "cash_balance",
}


def _parse_mapping(raw: str | None) -> dict[str, str] | None:
    """multipart 里的 `mapping` 是 JSON 字符串；非法一律 422 VALIDATION_ERROR。"""
    if raw is None or raw.strip() == "":
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ApiError(
            ErrorCode.VALIDATION_ERROR,
            errors=[FieldError("mapping", "invalid_json", "mapping 必须是 JSON 字符串")],
        ) from exc
    if not isinstance(data, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in data.items()
    ):
        raise ApiError(
            ErrorCode.VALIDATION_ERROR,
            errors=[FieldError("mapping", "invalid_mapping", "mapping 必须是 字符串→字符串")],
        )
    return data


def _preview_out(dto: ImportPreviewDTO) -> PreviewOut:
    """ledger 预览 DTO → API 边界 `PreviewOut`（金额在 sample 里已是定点字符串）。"""
    columns = [
        PreviewColumn(
            index=int(item["index"]),
            name=str(item["name"]),
            sample=([str(item["sample"])] if item.get("sample") else []),
            suggested_field=item.get("suggested_field"),
            confidence=float(item.get("confidence", 1.0)),
        )
        for item in dto.columns_detail
    ]
    warnings = [PreviewWarning(code=code, message=code) for code in dto.warnings]
    estimated = PreviewEstimated(
        parsed=dto.parsed_count,
        inserted=dto.estimated_inserted,
        skipped=dto.estimated_skipped,
        quarantined=dto.quarantined_count,
    )
    return PreviewOut(
        preview_id=dto.preview_id,
        file=PreviewFile(name=dto.file_name, sha256=dto.file_sha256, bytes=dto.file_bytes),
        columns=columns,
        date_format=dto.date_format,
        warnings=warnings,
        estimated=estimated,
        cash_balance_available=dto.cash_balance_available,
        can_commit=True,
        assumptions=dto.assumptions,
        sample=dto.sample,
        expires_at=dto.expires_at,
    )


def _batch_out(batch: Any) -> BatchOut:
    return BatchOut(
        batch_id=batch.id,
        status=batch.status,
        mode=batch.mode,
        broker=batch.broker,
        file_name=batch.file_name,
        counts=BatchCounts(
            parsed=batch.parsed_count,
            inserted=batch.inserted_count,
            skipped=batch.skipped_count,
            quarantined=batch.quarantined_count,
        ),
        warnings=list(batch.warnings),
        assumptions=dict(batch.assumptions),
        created_at=batch.created_at,
        finished_at=batch.finished_at,
        revoked_at=batch.revoked_at,
        can_revoke=(
            batch.revoked_at is None
            and batch.status
            in (
                ImportBatchStatus.PENDING.value,
                ImportBatchStatus.RUNNING.value,
                ImportBatchStatus.SUCCEEDED.value,
            )
        ),
    )


def _raw_out(raw: dict[str, Any], columns: dict[str, Any]) -> QuarantineRaw:
    """隔离区原始行（原始表头 → 值）按批次列映射回 canonical 字段。"""
    values: dict[str, Any] = {}
    for header, value in raw.items():
        field = columns.get(header)
        target = _QUARANTINE_FIELDS.get(str(field)) if field is not None else None
        if target is None or target in values:
            continue
        values[target] = str(value) if value is not None else None
    return QuarantineRaw(**values)


@router.post("/imports/preview", response_model=PreviewOut, operation_id="previewImport")
async def preview_import(
    request: Request,
    ctx: Annotated[TenantContext, Depends(get_tenant)],
    file: Annotated[UploadFile, File(description="账单文件 .xls/.xlsx/.csv")],
    mapping: Annotated[str | None, Form(description="表头→字段 的 JSON 字符串")] = None,
    date_format: Annotated[str | None, Form()] = None,
) -> PreviewOut:
    """API-010：解析账单、列映射建议、质量预检；同步 200，不写 ledger.trade。"""
    ctx.forbid_admin_trades()
    user_id = ctx.require_write()
    state = get_state(request)

    raw = await file.read()
    file_name = file.filename or "bill"
    parsed_mapping = _parse_mapping(mapping)

    async with state.db.session(user_id=user_id, begin=True) as session:
        dto = await create_preview(
            session,
            clock=state.clock,
            settings=state.settings,
            user_id=user_id,
            data=raw,
            file_name=file_name,
            mapping=parsed_mapping,
            date_format=date_format,
            object_store=state.object_store,
        )
    return _preview_out(dto)


@router.post(
    "/imports", status_code=202, response_model=JobAcceptedOut, operation_id="commitImport"
)
async def commit_import(
    request: Request,
    response: Response,
    payload: ImportCommitIn,
    ctx: Annotated[TenantContext, Depends(get_tenant)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> JobAcceptedOut | JSONResponse:
    """API-011：按确认映射提交导入（异步）。需 `Idempotency-Key`。"""
    ctx.forbid_admin_trades()
    user_id = ctx.require_write()
    state = get_state(request)

    if not idempotency_key or not 16 <= len(idempotency_key) <= 128:
        raise ApiError(
            ErrorCode.VALIDATION_ERROR,
            errors=[FieldError("Idempotency-Key", "required", "该操作必须携带幂等键(16~128)")],
        )
    if payload.mode == "replace_range" and payload.range is None:
        raise ApiError(
            ErrorCode.VALIDATION_ERROR,
            errors=[FieldError("range", "required", "replace_range 模式必须提供 range")],
        )
    if payload.range is not None and payload.range.from_date > payload.range.to_date:
        raise ApiError(
            ErrorCode.VALIDATION_ERROR,
            errors=[FieldError("range", "invalid", "range.from 不能晚于 range.to")],
        )

    body_json = json.dumps(payload.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    request_hash = hashlib.sha256(body_json.encode("utf-8")).hexdigest()

    async with state.db.session(user_id=user_id, begin=True) as session:
        try:
            record = await state.idempotency.begin(
                session,
                user_id=user_id,
                endpoint=_ENDPOINT_COMMIT,
                key=idempotency_key,
                request_hash=request_hash,
            )
        except ApiError as exc:
            if exc.code is ErrorCode.IDEMPOTENCY_IN_PROGRESS:
                job_ref = exc.metadata.get("job_id")
                raise ApiError(
                    ErrorCode.IDEMPOTENCY_IN_PROGRESS,
                    detail="该幂等键正在处理中",
                    headers={"X-Job-Id": str(job_ref)} if job_ref else {},
                ) from exc
            raise

        if record is not None:
            # 同键同体 → 重放首次响应（同一 job_id / 同一 body）。
            replayed = IdempotencyStore.payload_of(record)
            if replayed is not None:
                return JSONResponse(
                    content=replayed,
                    status_code=IdempotencyStore.status_of(record),
                    headers={
                        "Idempotency-Replayed": "true",
                        "Location": f"{_JOBS_PREFIX}/{replayed.get('job_id')}",
                        "Retry-After": "1",
                    },
                )

        preview = await ImportPreviewRepository(session).by_id(
            user_id, payload.preview_id, now=state.clock.now()
        )
        batch_repo = ImportBatchRepository(session)
        batch = await batch_repo.create(
            user_id,
            preview_id=payload.preview_id,
            mode=payload.mode,
            broker="huatai",
            file_name=preview.file_name,
            file_sha256=preview.file_sha256,
            file_bytes=preview.file_bytes,
            columns={},
            date_format=None,
            assumptions={},
            warnings=[],
            status=ImportBatchStatus.PENDING,
        )
        params: dict[str, Any] = {
            "preview_id": payload.preview_id,
            "mode": payload.mode,
            "batch_id": batch.id,
        }
        if payload.range is not None:
            # 区间替换必须用请求体声明的区间（不能拿账单 min/max 代替，否则会误删区间外数据）。
            params["range"] = payload.range.model_dump(by_alias=True, mode="json")
        if payload.note:
            params["note"] = payload.note
        job_id, reused = await state.queue.enqueue(
            session,
            kind="import_commit",
            user_id=user_id,
            scope_key=f"{user_id}:{payload.preview_id}",
            params=params,
        )

        batch_id = batch.id
        if reused:
            # 复用既有 job：丢弃本次多建的 PENDING 批次，沿用原 job 的批次。
            active = await state.queue.get(session, job_id)
            prior_batch_id = str((active.params or {}).get("batch_id") or "") if active else ""
            if prior_batch_id:
                await session.delete(batch)
                batch_id = prior_batch_id

        accepted = JobAcceptedOut(
            job_id=job_id,
            type="import_commit",
            batch_id=batch_id,
            status="pending",
            links=JobLinks(
                self=f"{_JOBS_PREFIX}/{job_id}", events=f"{_JOBS_PREFIX}/{job_id}/events"
            ),
        )
        await state.idempotency.complete(
            session,
            user_id=user_id,
            endpoint=_ENDPOINT_COMMIT,
            key=idempotency_key,
            status_code=202,
            body=accepted.model_dump(mode="json"),
            job_id=job_id,
        )

    response.headers["Location"] = f"{_JOBS_PREFIX}/{job_id}"
    response.headers["Retry-After"] = "1"
    if reused:
        response.headers["X-Job-Reused"] = "true"
    return accepted


@router.get("/imports", response_model=BatchPage, operation_id="listImports")
async def list_imports(
    request: Request,
    ctx: Annotated[TenantContext, Depends(get_tenant)],
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    cursor: Annotated[str | None, Query()] = None,
) -> BatchPage:
    """API-012：导入批次列表（游标分页，仅本人）。"""
    ctx.forbid_admin_trades()
    user_id = ctx.require_user()
    state = get_state(request)
    async with state.db.session(user_id=user_id, begin=True) as session:
        rows, next_cursor = await ImportBatchRepository(session).list_with_cursor(
            user_id, cursor=cursor, limit=limit
        )
        items = [_batch_out(row) for row in rows]
    return BatchPage(items=items, next_cursor=next_cursor)


@router.get("/imports/{batch_id}", response_model=BatchOut, operation_id="getImportBatch")
async def get_import_batch(
    batch_id: str,
    request: Request,
    ctx: Annotated[TenantContext, Depends(get_tenant)],
) -> BatchOut:
    """API-013：批次详情与计数；跨账号 404。"""
    ctx.forbid_admin_trades()
    user_id = ctx.require_user()
    state = get_state(request)
    async with state.db.session(user_id=user_id, begin=True) as session:
        batch = await ImportBatchRepository(session).by_id(user_id, batch_id)
        if batch is None:
            raise NotFoundError(detail=f"导入批次 {batch_id} 不存在")
        out = _batch_out(batch)
    return out


@router.get(
    "/imports/{batch_id}/quarantine",
    response_model=QuarantinePage,
    operation_id="listImportQuarantine",
)
async def list_import_quarantine(
    batch_id: str,
    request: Request,
    ctx: Annotated[TenantContext, Depends(get_tenant)],
    reason_code: Annotated[str | None, Query()] = None,
    resolved: Annotated[bool | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    cursor: Annotated[str | None, Query()] = None,
) -> QuarantinePage:
    """API-014：隔离区行（行号精确、raw 原样）；跨账号 404。"""
    ctx.forbid_admin_trades()
    user_id = ctx.require_user()
    state = get_state(request)
    async with state.db.session(user_id=user_id, begin=True) as session:
        batch = await ImportBatchRepository(session).by_id(user_id, batch_id)
        if batch is None:
            raise NotFoundError(detail=f"导入批次 {batch_id} 不存在")
        columns = dict(batch.columns)
        rows = await QuarantineRepository(session).list_for_batch(user_id, batch_id)

    filtered = [
        row
        for row in rows
        if (reason_code is None or row.reason_code == reason_code)
        and (resolved is None or row.resolved == resolved)
    ]
    if cursor is not None:
        try:
            after = int(cursor)
        except ValueError as exc:
            raise ApiError(ErrorCode.INVALID_CURSOR, detail="游标必须是整数") from exc
        filtered = [row for row in filtered if row.id > after]
    page = filtered[:limit]
    next_cursor = str(page[-1].id) if len(filtered) > limit and page else None
    items = [
        QuarantineItem(
            line_no=row.line_no,
            raw=_raw_out(row.raw, columns),
            reason_code=row.reason_code,
            suggested_rule_id=row.suggested_rule_id,
            resolved=row.resolved,
            created_at=row.created_at,
        )
        for row in page
    ]
    return QuarantinePage(items=items, next_cursor=next_cursor, total_estimate=len(filtered))


@router.post("/imports/{batch_id}/revoke", response_model=RevokeOut, operation_id="revokeImport")
async def revoke_import(
    batch_id: str,
    request: Request,
    ctx: Annotated[TenantContext, Depends(get_tenant)],
    payload: Annotated[RevokeIn | None, Body()] = None,
) -> RevokeOut:
    """API-015：同步撤销该批次（软删本批次写入行）；重复撤销 → 409。"""
    ctx.forbid_admin_trades()
    user_id = ctx.require_write()
    state = get_state(request)
    reason = payload.reason if payload and payload.reason else "user_request"
    async with state.db.session(user_id=user_id, begin=True) as session:
        outcome = await revoke_batch_now(
            session,
            user_id=user_id,
            batch_id=batch_id,
            clock=state.clock,
            reason=reason,
            actor_id=user_id,
            request_id=request.headers.get("x-request-id"),
        )
    return RevokeOut(
        batch_id=outcome["batch_id"],
        status="revoked",
        reverted={"inserted": int(outcome["removed_trades"]), "updated": 0},
        revoked_at=outcome["revoked_at"],
    )
