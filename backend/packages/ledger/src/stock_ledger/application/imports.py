"""导入第二阶段：提交 / 撤销（worker 任务处理器）。

提交必须幂等（`preview.consumed_at` 是锚点）、可中断（progress + 取消检查）、可撤销
（记录写入批次，撤销时只软删“本批次写入且未被人工改过”的行）。中途把进度通过
`queue.progress/emit` 上报，是为了让前端 SSE 能实时看到“解析→写入→收尾”。
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession
from stock_contracts.clock import Clock
from stock_contracts.enums import ImportBatchStatus, JobStatus
from stock_contracts.errors import ApiError, ErrorCode, NotFoundError
from stock_platform.audit import record
from stock_platform.jobs import JobQueue
from stock_platform.outbox import publish

from stock_ledger.application.preview import default_object_store
from stock_ledger.parsers.huatai import ParsedBill, parse_bill
from stock_ledger.repositories.imports import ImportBatchRepository, ImportPreviewRepository
from stock_ledger.repositories.quarantine import QuarantineRepository
from stock_ledger.repositories.trades import TradeRepository, TradeRow

EVENT_COMMITTED = "ledger.import.committed"
EVENT_REVOKED = "ledger.import.revoked"


async def _fail_job(
    queue: JobQueue, session: AsyncSession, job_id: str, exc: BaseException
) -> dict[str, Any]:
    """失败收口：记失败/重试；不向 runner 抛异常（避免打挂消费循环）。

    半截写入由调用方在开头打开的**保存点**回滚（`session.begin_nested()`），
    这里**不能**再 `session.rollback()`：那会终结调用方（worker/API/CLI）的外层事务，
    后续 SQL 会抛 "Can't operate on closed transaction inside context manager"。
    """
    if isinstance(exc, ApiError):
        code = exc.code.value
        error: dict[str, Any] = {"code": code, "detail": exc.detail}
    else:
        code = ErrorCode.INTERNAL_ERROR.value
        error = {"code": code, "detail": str(exc)}
    status = await queue.fail_or_retry(session, job_id, error_code=code, error=error)
    return {"job_id": job_id, "status": status.value, "error_code": code}


def _bill_to_trade_rows(bill: ParsedBill, batch_id: str) -> list[TradeRow]:
    return [
        TradeRow(
            trade_date=row.trade_date,
            security_code=row.security_code,
            security_name=row.security_name,
            side=row.side,
            quantity_units=row.quantity_units,
            price_units=row.price_units,
            amount_units=row.amount_units,
            dedupe_key=row.dedupe_key,
            batch_id=batch_id,
            summary=row.summary,
            summary_code=row.summary_code,
            commission_units=row.commission_units,
            stamp_tax_units=row.stamp_tax_units,
            other_fee_units=row.other_fee_units,
            cash_balance_units=row.cash_balance_units,
            cash_balance_available=row.cash_balance_available,
            occurrence_idx=row.occurrence_idx,
            account_masked=row.account_masked,
            line_no=row.line_no,
        )
        for row in bill.rows
    ]


async def commit_batch(
    queue: JobQueue, session: AsyncSession, job: dict[str, Any], clock: Clock
) -> dict[str, Any]:
    """提交一次导入：解析原文件 → 去重去孤 → 写明细 → 收口批次 + 留痕。"""
    user_id = uuid.UUID(str(job["user_id"]))
    job_id = str(job["id"])
    params: dict[str, Any] = dict(job.get("params") or {})
    preview_id = str(params.get("preview_id") or "")
    mode = str(params.get("mode") or "append")
    batch_repo = ImportBatchRepository(session)
    preview_repo = ImportPreviewRepository(session)

    try:
        # 保存点：失败时只回滚本次半截写入，不终结调用方外层事务。
        savepoint = await session.begin_nested()
        # 短路仅针对已收口的批次；PENDING 批次是 API 侧预建的“待收养”批次，不能短路。
        existing = await batch_repo.by_preview(user_id, preview_id)
        if existing is not None and existing.status != ImportBatchStatus.PENDING.value:
            return _committed_result(existing, reused=True)

        preview = await preview_repo.by_id(user_id, preview_id, now=clock.now())
        await queue.progress(session, job_id, percent=5, stage="parsing", message=None)

        data = await default_object_store().get_bytes(preview.object_key)
        bill = parse_bill(
            data,
            preview.file_name,
            mapping=dict(preview.mapping) or None,
            date_format=preview.date_format,
            clock=clock,
        )

        if await queue.is_cancel_requested(session, job_id):
            await queue.finish(
                session, job_id, status=JobStatus.CANCELLED, result={"cancelled": True}
            )
            return {"job_id": job_id, "cancelled": True}

        await queue.progress(session, job_id, percent=35, stage="writing", message=None)

        adopted_id = params.get("batch_id")
        if adopted_id:
            # 收养 API-011 同步创建的 PENDING 批次（否则会出现两条批次）。
            batch = await batch_repo.by_id(user_id, str(adopted_id))
            if batch is None:
                raise NotFoundError(detail=f"导入批次 {adopted_id} 不存在")
            batch.broker = "huatai"
            batch.file_name = preview.file_name
            batch.file_sha256 = preview.file_sha256
            batch.file_bytes = preview.file_bytes
            batch.columns = bill.columns
            batch.date_format = bill.date_format
            batch.assumptions = bill.assumptions
            batch.warnings = [warning.value for warning in bill.warnings]
            await session.flush()
        else:
            batch = await batch_repo.create(
                user_id,
                preview_id=preview_id,
                mode=mode,
                broker="huatai",
                file_name=preview.file_name,
                file_sha256=preview.file_sha256,
                file_bytes=preview.file_bytes,
                columns=bill.columns,
                date_format=bill.date_format,
                assumptions=bill.assumptions,
                warnings=[warning.value for warning in bill.warnings],
            )

        trade_repo = TradeRepository(session)
        date_from = min(row.trade_date for row in bill.rows) if bill.rows else None
        date_to = max(row.trade_date for row in bill.rows) if bill.rows else None
        replaced = 0
        if mode == "replace_range" and date_from is not None and date_to is not None:
            replaced = await trade_repo.soft_delete_range(
                user_id, date_from=date_from, date_to=date_to, at=clock.now()
            )

        existing_keys = await trade_repo.existing_dedupe_keys(
            user_id, [row.dedupe_key for row in bill.rows]
        )
        seen: set[str] = set()
        to_insert: list[TradeRow] = []
        skipped = 0
        for row in _bill_to_trade_rows(bill, batch.id):
            if row.dedupe_key in existing_keys or row.dedupe_key in seen:
                skipped += 1
                continue
            seen.add(row.dedupe_key)
            to_insert.append(row)
        inserted = await trade_repo.insert_many(user_id, to_insert)

        if bill.quarantined:
            await QuarantineRepository(session).add_many(
                user_id,
                [
                    {
                        "batch_id": batch.id,
                        "line_no": item.line_no,
                        "raw": item.raw,
                        "reason_code": item.reason_code,
                        "suggested_rule_id": item.suggested_rule_id,
                    }
                    for item in bill.quarantined
                ],
            )

        await queue.progress(session, job_id, percent=85, stage="finalizing", message=None)
        ranges: dict[str, Any] = (
            {"from": date_from.isoformat(), "to": date_to.isoformat()}
            if date_from is not None and date_to is not None
            else {}
        )
        ranges["replaced_trades"] = replaced
        finished = await batch_repo.finish(
            user_id,
            batch.id,
            status=ImportBatchStatus.SUCCEEDED,
            parsed_count=len(bill.rows),
            inserted_count=inserted,
            skipped_count=skipped,
            quarantined_count=len(bill.quarantined),
            ranges=ranges,
            at=clock.now(),
        )
        await preview_repo.mark_consumed(user_id, preview_id, at=clock.now())

        await publish(
            session,
            event_type=EVENT_COMMITTED,
            aggregate="import_batch",
            aggregate_id=finished.id,
            user_id=user_id,
            payload={
                "batch_id": finished.id,
                "inserted": inserted,
                "skipped": skipped,
                "quarantined": len(bill.quarantined),
            },
        )
        await record(
            session,
            action="import.commit",
            actor_id=user_id,
            target_type="import_batch",
            target_id=finished.id,
            meta={"mode": mode, "inserted": inserted, "skipped": skipped},
        )

        result = _committed_result(finished, reused=False)
        await queue.finish(session, job_id, status=JobStatus.SUCCEEDED, result=result)
        await savepoint.commit()
        return result
    except BaseException as exc:
        if session.in_nested_transaction():
            await savepoint.rollback()
        return await _fail_job(queue, session, job_id, exc)


def _committed_result(batch: Any, *, reused: bool) -> dict[str, Any]:
    return {
        "batch_id": batch.id,
        "status": batch.status,
        "inserted": batch.inserted_count,
        "skipped": batch.skipped_count,
        "quarantined": batch.quarantined_count,
        "reused": reused,
    }


async def revoke_batch_now(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    batch_id: str,
    clock: Clock,
    reason: str = "user_request",
    actor_id: uuid.UUID | None = None,
    request_id: str | None = None,
) -> dict[str, Any]:
    """同步撤销：只软删该批次写入且 version=1 的行，并置批次为 revoked。

    不依赖 job 队列（API-015 需要同步 200）；job 版本 `revoke_batch` 复用它。
    已撤销 → IMPORT_ALREADY_REVOKED(409)；批次不存在/跨账号 → 404。
    """
    batch_repo = ImportBatchRepository(session)
    trade_repo = TradeRepository(session)
    if await batch_repo.undone(user_id, batch_id):
        raise ApiError(ErrorCode.IMPORT_ALREADY_REVOKED, detail="批次已撤销")

    removed = await trade_repo.soft_delete_by_batch(
        user_id, batch_id, at=clock.now(), only_version_1=True
    )
    batch = await batch_repo.revoke(user_id, batch_id, reason=reason, at=clock.now())

    await publish(
        session,
        event_type=EVENT_REVOKED,
        aggregate="import_batch",
        aggregate_id=batch.id,
        user_id=user_id,
        payload={"batch_id": batch.id, "removed_trades": removed, "reason": reason},
    )
    await record(
        session,
        action="import.revoke",
        actor_id=actor_id or user_id,
        target_type="import_batch",
        target_id=batch.id,
        meta={"removed_trades": removed, "reason": reason},
        request_id=request_id,
        source="api",
    )
    return {
        "batch_id": batch.id,
        "status": batch.status,
        "revoked": True,
        "removed_trades": removed,
        "revoked_at": batch.revoked_at,
    }


async def revoke_batch(
    queue: JobQueue, session: AsyncSession, job: dict[str, Any], clock: Clock
) -> dict[str, Any]:
    """job 版撤销（复用 `revoke_batch_now` + 进度/收口）。"""
    user_id = uuid.UUID(str(job["user_id"]))
    job_id = str(job["id"])
    params: dict[str, Any] = dict(job.get("params") or {})
    batch_id = str(params.get("batch_id") or "")
    reason = str(params.get("reason") or "user_request")
    try:
        savepoint = await session.begin_nested()
        await queue.progress(session, job_id, percent=20, stage="writing", message=None)
        outcome = await revoke_batch_now(
            session, user_id=user_id, batch_id=batch_id, clock=clock, reason=reason
        )
        result = {
            "batch_id": outcome["batch_id"],
            "revoked": True,
            "removed_trades": outcome["removed_trades"],
        }
        await queue.finish(session, job_id, status=JobStatus.SUCCEEDED, result=result)
        await savepoint.commit()
        return result
    except BaseException as exc:
        if session.in_nested_transaction():
            await savepoint.rollback()
        return await _fail_job(queue, session, job_id, exc)


__all__ = ["commit_batch", "revoke_batch", "revoke_batch_now"]
