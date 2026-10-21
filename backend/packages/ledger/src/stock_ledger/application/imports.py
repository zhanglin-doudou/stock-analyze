"""导入第二阶段：提交 / 撤销（worker 任务处理器）。

提交必须幂等（`preview.consumed_at` 是锚点）、可中断（progress + 取消检查）、可撤销
（记录写入批次，撤销时只软删“本批次写入且未被人工改过”的行）。中途把进度通过
`queue.progress/emit` 上报，是为了让前端 SSE 能实时看到“解析→分类→写入→收尾”。

G3：提交阶段逐行分类（默认+用户摘要规则）——命中写 `category/summary_code` 并落库；
未命中不写 trade，改落隔离区 `UNKNOWN_SUMMARY`（含建议规则/行号），计数与 preview 一致。
"""

from __future__ import annotations

import uuid
from datetime import date
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession
from stock_contracts.clock import Clock
from stock_contracts.enums import ImportBatchStatus, JobStatus, QuarantineReason, WarningCode
from stock_contracts.errors import ApiError, ErrorCode, FieldError, NotFoundError
from stock_platform.audit import record
from stock_platform.jobs import JobQueue
from stock_platform.outbox import publish

from stock_ledger.application.classify import load_rules, match_rule, suggest_rule
from stock_ledger.application.preview import default_object_store
from stock_ledger.models import SummaryRule
from stock_ledger.parsers.huatai import ParsedRow, parse_bill
from stock_ledger.repositories.imports import ImportBatchRepository, ImportPreviewRepository
from stock_ledger.repositories.quarantine import QuarantineRepository
from stock_ledger.repositories.rules import rule_summary_code
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


def _requested_range(params: dict[str, Any]) -> tuple[date | None, date | None]:
    """解析 API-011 请求里的 `range`（`{"from": .., "to": ..}`）。

    缺省返回 (None, None)：兼容 CLI/seed 等未携带 range 的旧调用方（按账单日期兜底）。
    非法格式/倒置区间一律 422 参数错误，避免静默替换错区间。
    """
    raw = params.get("range")
    if raw is None:
        return None, None
    if not isinstance(raw, dict):
        raise ApiError(
            ErrorCode.VALIDATION_ERROR,
            errors=[FieldError("range", "invalid", "range 必须是 {from,to} 对象")],
        )
    try:
        start = date.fromisoformat(str(raw.get("from") or raw.get("from_date")))
        end = date.fromisoformat(str(raw.get("to") or raw.get("to_date")))
    except ValueError as exc:
        raise ApiError(
            ErrorCode.VALIDATION_ERROR,
            errors=[FieldError("range", "invalid", "range 日期必须为 YYYY-MM-DD")],
        ) from exc
    if start > end:
        raise ApiError(
            ErrorCode.VALIDATION_ERROR,
            errors=[FieldError("range", "invalid", "range.from 不能晚于 range.to")],
        )
    return start, end


def _parsed_to_trade_row(parsed: ParsedRow, batch_id: str) -> TradeRow:
    """解析行 → 待插入流水行（分类字段由调用方按规则写入）。"""
    return TradeRow(
        trade_date=parsed.trade_date,
        security_code=parsed.security_code,
        security_name=parsed.security_name,
        side=parsed.side,
        quantity_units=parsed.quantity_units,
        price_units=parsed.price_units,
        amount_units=parsed.amount_units,
        dedupe_key=parsed.dedupe_key,
        batch_id=batch_id,
        summary=parsed.summary,
        summary_code=parsed.summary_code,
        commission_units=parsed.commission_units,
        stamp_tax_units=parsed.stamp_tax_units,
        other_fee_units=parsed.other_fee_units,
        cash_balance_units=parsed.cash_balance_units,
        cash_balance_available=parsed.cash_balance_available,
        occurrence_idx=parsed.occurrence_idx,
        account_masked=parsed.account_masked,
        line_no=parsed.line_no,
    )


def _unknown_summary_quarantine_rows(
    parsed_rows: list[ParsedRow], *, batch_id: str, rules: list[SummaryRule]
) -> list[dict[str, Any]]:
    """分类未命中的行 → 隔离区记录（不写 trade，避免未分类数据污染统计）。"""
    return [
        {
            "batch_id": batch_id,
            "line_no": parsed.line_no,
            "raw": {
                **parsed.raw,
                "summary": parsed.summary or "",
                "line_no": parsed.line_no,
            },
            "reason_code": QuarantineReason.UNKNOWN_SUMMARY.value,
            "suggested_rule_id": suggest_rule(rules, parsed.summary, parsed.side),
        }
        for parsed in parsed_rows
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
        bill_from = min(row.trade_date for row in bill.rows) if bill.rows else None
        bill_to = max(row.trade_date for row in bill.rows) if bill.rows else None
        replace_from: date | None = None
        replace_to: date | None = None
        replaced = 0
        if mode == "replace_range":
            requested_from, requested_to = _requested_range(params)
            # 旧调用方（CLI/seed）不带 range 时按账单日期兜底，保持向后兼容。
            replace_from = requested_from or bill_from
            replace_to = requested_to or bill_to
            if replace_from is None or replace_to is None:
                raise ApiError(
                    ErrorCode.VALIDATION_ERROR,
                    errors=[FieldError("range", "required", "replace_range 模式必须提供 range")],
                )
            replaced = await trade_repo.soft_delete_range(
                user_id, date_from=replace_from, date_to=replace_to, at=clock.now()
            )

        # 分类：与 preview 同一套规则与同一执行顺序（dedupe → 分类），保证隔离计数一致。
        rules = await load_rules(session, user_id)
        existing_keys = await trade_repo.existing_dedupe_keys(
            user_id, [row.dedupe_key for row in bill.rows]
        )
        seen: set[str] = set()
        to_insert: list[TradeRow] = []
        unknown_summary_rows: list[ParsedRow] = []
        skipped = 0
        for parsed in bill.rows:
            if parsed.dedupe_key in existing_keys or parsed.dedupe_key in seen:
                skipped += 1
                continue
            seen.add(parsed.dedupe_key)
            rule = match_rule(rules, parsed.summary, parsed.side)
            if rule is None:
                unknown_summary_rows.append(parsed)
                continue
            trade_row = _parsed_to_trade_row(parsed, batch.id)
            trade_row.category = rule.category
            trade_row.summary_code = rule_summary_code(rule)
            to_insert.append(trade_row)
        inserted = await trade_repo.insert_many(user_id, to_insert)

        # 批次警告/假设与 preview 对齐：未知摘要也要打 UNKNOWN_SUMMARY 并留行号明细。
        warnings = [warning.value for warning in bill.warnings]
        assumptions: dict[str, Any] = dict(bill.assumptions)
        if unknown_summary_rows:
            warnings.append(WarningCode.UNKNOWN_SUMMARY.value)
            assumptions["unknown_summary_rows"] = [
                {"line_no": row.line_no, "summary": row.summary, "side": row.side}
                for row in unknown_summary_rows
            ]
        batch.warnings = warnings
        batch.assumptions = assumptions

        quarantine_payloads: list[dict[str, Any]] = [
            {
                "batch_id": batch.id,
                "line_no": item.line_no,
                "raw": item.raw,
                "reason_code": item.reason_code,
                "suggested_rule_id": item.suggested_rule_id,
            }
            for item in bill.quarantined
        ]
        quarantine_payloads.extend(
            _unknown_summary_quarantine_rows(unknown_summary_rows, batch_id=batch.id, rules=rules)
        )
        if quarantine_payloads:
            await QuarantineRepository(session).add_many(user_id, quarantine_payloads)

        await queue.progress(session, job_id, percent=85, stage="finalizing", message=None)
        range_from = replace_from if mode == "replace_range" else bill_from
        range_to = replace_to if mode == "replace_range" else bill_to
        ranges: dict[str, Any] = (
            {"from": range_from.isoformat(), "to": range_to.isoformat()}
            if range_from is not None and range_to is not None
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
            quarantined_count=len(quarantine_payloads),
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
                "quarantined": len(quarantine_payloads),
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
