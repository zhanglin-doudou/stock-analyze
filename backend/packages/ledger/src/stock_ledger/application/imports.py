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
from stock_contracts.enums import (
    ImportBatchStatus,
    ImportStage,
    JobStatus,
    QuarantineReason,
    WarningCode,
)
from stock_contracts.errors import ApiError, ErrorCode, FieldError, NotFoundError
from stock_platform.audit import record
from stock_platform.jobs import JobQueue
from stock_platform.obs import get_logger
from stock_platform.outbox import publish

from stock_ledger.application.cash_leg import (
    attribute_cash_legs_from_db,
    is_real_security,
)
from stock_ledger.application.chain import (
    ChainMode,
    apply_multi_chain_plan,
    plan_chain_by_account,
)
from stock_ledger.application.classify import (
    category_after_amount_fallback,
    load_rules,
    match_rule,
    side_from_category,
    suggest_rule,
)
from stock_ledger.application.import_analysis import row_type_key
from stock_ledger.application.preview import default_object_store
from stock_ledger.models import SummaryRule
from stock_ledger.parsers.huatai import ParsedRow, parse_bill, parse_bill_with_plan
from stock_ledger.parsers.mapping import MappingItem, MappingPlan
from stock_ledger.repositories.import_mapping import (
    ImportBillTypeRepository,
    ImportTemplateRepository,
)
from stock_ledger.repositories.imports import ImportBatchRepository, ImportPreviewRepository
from stock_ledger.repositories.quarantine import QuarantineRepository
from stock_ledger.repositories.rules import rule_summary_code
from stock_ledger.repositories.trades import TradeRepository, TradeRow
from stock_ledger.template_presets import system_template_by_id

log = get_logger("stock_ledger.imports")

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


# 卖出缺数量/单价兜底：华泰资管转让的货基赎回只落资金腿（有金额、无数量、无单价）。
# 货基单位净值恒 1.000000；金额 scale=2、数量 scale=4，反推数量 = |金额| × 100。
SELL_FILL_PRICE_UNITS = 1_000_000
SELL_FILL_QTY_MULTIPLIER = 100


def fill_sell_quantity_units(row: TradeRow) -> None:
    """落库前归一：真实证券的卖出只有资金变更、没有数量/单价时，按货基 1 元净值补数量。

    仅在 `category=sell`、`amount_units != 0`、`quantity_units == 0`、
    `price_units == 0` 且证券代码是真实 6 位代码时生效；伪代码（`CASH`/空）的资金腿
    走 `cash_leg` 的份额腿配对与货基兜底，不再事后补成 CASH 货基。
    """
    if (
        (row.category or "").strip().lower() == "sell"
        and row.amount_units != 0
        and row.quantity_units == 0
        and row.price_units == 0
        and is_real_security(row.security_code)
    ):
        row.quantity_units = abs(row.amount_units) * SELL_FILL_QTY_MULTIPLIER
        row.price_units = SELL_FILL_PRICE_UNITS


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


def _snapshot_value(snapshot: Any, key: str, default: Any = None) -> Any:
    if isinstance(snapshot, dict):
        return snapshot.get(key, default)
    return getattr(snapshot, key, default)


def _plan_from_snapshot(snapshot: Any, *, ignored_columns: list[str]) -> MappingPlan:
    """用提交时落入批次/任务的快照构造计划，不重读可变 preview 草稿。"""
    return MappingPlan(
        header_row_index=_snapshot_value(snapshot, "header_row_index") or 0,
        items=tuple(
            MappingItem.from_dict(item)
            for item in (_snapshot_value(snapshot, "mapping_items", []) or [])
        ),
        ignored=tuple(ignored_columns),
    )


def _discarded_quarantine_rows(
    parsed_rows: list[ParsedRow], *, batch_id: str
) -> list[dict[str, Any]]:
    """用户标记为废弃的账单类型：落隔离区但单独计 `discarded_count`。"""
    return [
        {
            "batch_id": batch_id,
            "line_no": parsed.line_no,
            "raw": {
                **parsed.raw,
                "summary": parsed.summary or "",
                "line_no": parsed.line_no,
            },
            "reason_code": QuarantineReason.BILL_TYPE_DISCARDED.value,
            "suggested_rule_id": None,
        }
        for parsed in parsed_rows
    ]


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
            "suggested_rule_id": suggest_rule(rules, parsed.summary),
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
            fill_counts = await TradeRepository(session).count_fills_by_batches(
                user_id, [existing.id]
            )
            return _committed_result(
                existing, reused=True, filled_count=fill_counts.get(existing.id, 0)
            )

        preview = await preview_repo.by_id(user_id, preview_id, now=clock.now())
        await queue.progress_committed(user_id, job_id, percent=5, stage="parsing", message=None)

        adopted_id = params.get("batch_id")
        batch = await batch_repo.by_id(user_id, str(adopted_id)) if adopted_id is not None else None
        if adopted_id is not None and batch is None:
            raise NotFoundError(detail=f"导入批次 {adopted_id} 不存在")

        # v2 的权威输入是提交时落入批次/任务的快照；preview 只提供原文件与 legacy 兼容字段。
        snapshot: Any = batch if batch is not None else params
        snapshot_stage = str(params.get("stage") or preview.stage)
        legacy = snapshot_stage == ImportStage.LEGACY.value
        if not legacy and snapshot_stage != ImportStage.READY.value:
            raise ApiError(
                ErrorCode.IMPORT_STAGE_CONFLICT,
                detail="预览尚未完成列对应与类型确认，不能提交",
            )
        ignored_columns = [str(item) for item in (params.get("ignored_columns") or [])]
        if "ignored_columns" not in params and legacy:
            ignored_columns = list(preview.ignored_columns or [])
        snapshot_date_format = params.get("date_format")
        if snapshot_date_format is None and "date_format" not in params:
            snapshot_date_format = preview.date_format
        plan = None if legacy else _plan_from_snapshot(snapshot, ignored_columns=ignored_columns)
        data = await default_object_store().get_bytes(preview.object_key)
        if legacy:
            bill = parse_bill(
                data,
                preview.file_name,
                mapping=(
                    dict(params.get("mapping") or {})
                    if "mapping" in params
                    else dict(preview.mapping or {})
                )
                or None,
                date_format=snapshot_date_format,
                clock=clock,
            )
        else:
            assert plan is not None
            broker = str(params.get("broker") or _snapshot_value(snapshot, "broker"))
            bill = parse_bill_with_plan(
                data,
                preview.file_name,
                plan=plan,
                date_format=snapshot_date_format,
                clock=clock,
                broker=broker,
            )
        broker = "huatai" if legacy else broker
        mapping_items = list(_snapshot_value(snapshot, "mapping_items", []) or [])
        type_resolutions = list(_snapshot_value(snapshot, "type_resolutions", []) or [])
        signature = _snapshot_value(snapshot, "signature") if not legacy else None
        template_id = _snapshot_value(snapshot, "template_id") if not legacy else None
        mapping_source = (
            ImportStage.LEGACY.value
            if legacy
            else str(
                _snapshot_value(snapshot, "mapping_source")
                or params.get("mapping_source")
                or "manual"
            )
        )
        # 写库前先做对账链规划：选起点、断行先隔离、覆盖区段以文件为准。
        # 导入链路不再自动补差（补差只保留给独立对账 API）。
        chain = await plan_chain_by_account(
            session,
            user_id=user_id,
            rows=bill.rows,
            force_replace=mode == "replace_range",
        )
        apply_multi_chain_plan(bill, chain)
        covered = chain.mode == ChainMode.COVERED
        if covered:
            # 整文件已被库内覆盖：no-op，不写明细、不写隔离、不写记忆。
            bill.quarantined = []

        if await queue.is_cancel_requested(session, job_id):
            await queue.finish(
                session, job_id, status=JobStatus.CANCELLED, result={"cancelled": True}
            )
            return {"job_id": job_id, "cancelled": True}

        await queue.progress_committed(user_id, job_id, percent=35, stage="writing", message=None)

        if batch is not None:
            # 收养 API-011 同步创建的 PENDING 批次；快照字段保持提交时原值。
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
                broker=broker,
                file_name=preview.file_name,
                file_sha256=preview.file_sha256,
                file_bytes=preview.file_bytes,
                columns=bill.columns,
                date_format=bill.date_format,
                assumptions=bill.assumptions,
                warnings=[warning.value for warning in bill.warnings],
                template_id=template_id,
                signature=signature,
                header_row_index=(
                    _snapshot_value(snapshot, "header_row_index") if not legacy else None
                ),
                mapping_fingerprint=(
                    _snapshot_value(snapshot, "mapping_fingerprint") if not legacy else None
                ),
                mapping_items=mapping_items,
                type_resolutions=type_resolutions,
                mapping_source=mapping_source,
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
        if mode == "replace_range":
            if replace_from is not None and replace_to is not None:
                replaced = await trade_repo.soft_delete_range(
                    user_id, date_from=replace_from, date_to=replace_to, at=clock.now()
                )
        else:
            # 对账链判定的覆盖区段以文件为准：按子账户分别软删各自区段后整段重写。
            for account, range_from, range_to in chain.replace_ranges():
                replaced += await trade_repo.soft_delete_range(
                    user_id,
                    date_from=range_from,
                    date_to=range_to,
                    at=clock.now(),
                    account=account,
                )

        # 分类：与 preview 同一顺序（dedupe → 类型/规则），保证隔离计数一致。
        # legacy 走摘要规则；v2 以 `type_resolutions` 快照为准（stage=ready 已保证全覆盖）。
        rules = await load_rules(session, user_id)
        rules_by_id = {rule.id: rule for rule in rules}
        resolutions = (
            {} if legacy else {str(item.get("type_key")): item for item in type_resolutions}
        )
        existing_keys = await trade_repo.existing_dedupe_keys(
            user_id, [row.dedupe_key for row in bill.rows]
        )
        seen: set[str] = set()
        to_insert: list[TradeRow] = []
        unknown_summary_rows: list[ParsedRow] = []
        discarded_rows: list[ParsedRow] = []
        skipped = chain.skipped
        for parsed in bill.rows:
            if parsed.dedupe_key in existing_keys or parsed.dedupe_key in seen:
                skipped += 1
                continue
            seen.add(parsed.dedupe_key)
            if legacy:
                rule = match_rule(rules, parsed.summary)
                if rule is None:
                    unknown_summary_rows.append(parsed)
                    continue
                trade_row = _parsed_to_trade_row(parsed, batch.id)
                # 零金额登记动作自动降级（摘要含"新股"但 amount==0 → broker_action）
                final_category = category_after_amount_fallback(rule.category, parsed.amount_units)
                trade_row.category = final_category
                trade_row.side = side_from_category(final_category)
                trade_row.summary_code = rule_summary_code(rule)
                to_insert.append(trade_row)
                continue
            type_key, _ = row_type_key(parsed)
            resolution = resolutions.get(type_key)
            if resolution is None:
                raise ApiError(
                    ErrorCode.IMPORT_BILL_TYPES_UNRESOLVED,
                    detail=f"账单类型 {type_key} 未确认，不能提交",
                )
            if bool(resolution.get("discard")):
                discarded_rows.append(parsed)
                continue
            category = resolution.get("category")
            if not category:
                raise ApiError(
                    ErrorCode.IMPORT_BILL_TYPES_UNRESOLVED,
                    detail=f"账单类型 {type_key} 缺少分类，不能提交",
                )
            trade_row = _parsed_to_trade_row(parsed, batch.id)
            trade_row.category = str(category)
            trade_row.side = side_from_category(str(category))
            rule_id = resolution.get("suggested_rule_id")
            matched_rule = rules_by_id.get(str(rule_id)) if rule_id else None
            trade_row.summary_code = rule_summary_code(matched_rule) if matched_rule else None
            to_insert.append(trade_row)
        # 落库前归一：只有金额的资金腿先配对同日份额腿，再按货基持仓/现金兜底。
        to_insert = await attribute_cash_legs_from_db(session, user_id, to_insert)
        for trade_row in to_insert:
            fill_sell_quantity_units(trade_row)
        inserted = await trade_repo.insert_many(user_id, to_insert)

        # 批次警告/假设与 preview 对齐：未知摘要也要打 UNKNOWN_SUMMARY 并留行号明细。
        warnings = [
            warning.value for warning in bill.warnings if warning != WarningCode.UNKNOWN_SUMMARY
        ]
        assumptions: dict[str, Any] = dict(bill.assumptions)
        if unknown_summary_rows:
            warnings.append(WarningCode.UNKNOWN_SUMMARY.value)
            assumptions["unknown_summary_rows"] = [
                {"line_no": row.line_no, "summary": row.summary} for row in unknown_summary_rows
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
        quarantine_payloads.extend(_discarded_quarantine_rows(discarded_rows, batch_id=batch.id))
        if quarantine_payloads:
            await QuarantineRepository(session).add_many(user_id, quarantine_payloads)

        await queue.progress_committed(
            user_id, job_id, percent=85, stage="finalizing", message=None
        )
        coverage_from = replace_from if replace_from is not None else bill_from
        coverage_to = replace_to if replace_to is not None else bill_to
        ranges: dict[str, Any] = (
            {"from": coverage_from.isoformat(), "to": coverage_to.isoformat()}
            if coverage_from is not None and coverage_to is not None
            else {}
        )
        ranges["replaced_trades"] = replaced
        ranges["coverage"] = chain.coverage.get("mode", ChainMode.EMPTY.value)

        # 导入链路停用自动补差：缺口只做链式隔离，不再写 side=missing 补差行。
        fill_count = 0

        batch.discarded_count = len(discarded_rows)
        finished = await batch_repo.finish(
            user_id,
            batch.id,
            status=ImportBatchStatus.SUCCEEDED,
            # parsed = 进入插入循环的行（接受行 + 因锚点已存在而跳过的行）；
            # 链式隔离行只进 quarantined，不计入 parsed。
            parsed_count=len(bill.rows) + chain.skipped,
            inserted_count=inserted,
            skipped_count=skipped,
            # 废弃类型单独计数（`discarded_count`），不并入 quarantined（设计 D-12）。
            quarantined_count=len(bill.quarantined) + len(unknown_summary_rows),
            ranges=ranges,
            at=clock.now(),
        )
        await preview_repo.mark_consumed(user_id, preview_id, at=clock.now())

        # 用户级记忆（模板/类型）：只在 v2 成功提交时写；写前再读一次撤销标记，
        # 避免「已撤销批次又被 worker 写回孤儿记忆」（设计 §7.2 第 6 步）。
        templates_written = 0
        types_written = 0
        types_modified_existing = 0
        if not legacy and finished.revoked_at is None and not covered:
            template_repo = ImportTemplateRepository(session)
            # 套用系统预设时复制预设的全部类型（不只是本文件出现的）：
            # 否则增量账单里首次出现的类型在下次导入时命不中已保存的模板。
            preset = system_template_by_id(str(template_id)) if template_id else None
            memory_resolutions = list(type_resolutions)
            if preset is not None:
                known_keys = {str(item.get("type_key")) for item in memory_resolutions}
                for preset_type in preset.bill_types:
                    if preset_type.type_key in known_keys:
                        continue
                    memory_resolutions.append(
                        {
                            "type_key": preset_type.type_key,
                            "type_label": preset_type.type_key,
                            "category": preset_type.category,
                            "discard": preset_type.discarded,
                            "source": "template",
                        }
                    )
            types_written, types_modified_existing = await ImportBillTypeRepository(
                session
            ).upsert_many(
                user_id,
                memory_resolutions,
                batch_id=finished.id,
                broker=broker,
            )
            if signature and mapping_items:
                # 类型先落库拿到 id，再按界面顺序（含废弃）整体覆盖模板引用；
                # 套用系统预设时 memory_resolutions 已补齐预设全部类型，
                # 增量账单里新出现的类型无需再次确认。
                type_rows = await ImportBillTypeRepository(session).by_keys(
                    user_id, [str(item["type_key"]) for item in memory_resolutions]
                )
                bill_type_ids = [
                    type_rows[key].id
                    for item in memory_resolutions
                    if (key := str(item.get("type_key"))) in type_rows
                ]
                template = await template_repo.upsert(
                    user_id,
                    broker=broker,
                    signature=str(signature),
                    header_count=len(bill.columns) + len(bill.unresolved),
                    date_format=bill.date_format,
                    items=mapping_items,
                    ignored=ignored_columns,
                    origin_batch_id=finished.id,
                    last_batch_id=finished.id,
                    bill_type_ids=bill_type_ids,
                )
                finished.template_id = template.id
                templates_written = 1

        # 历史类型被改动分类/废弃 → 影响已解析流水的 category，触发全量 reclassify 订正。
        # 仅当本批次真正改了历史类型才触发；新增类型或未改动的复用不触发。
        if types_modified_existing > 0:
            try:
                await queue.enqueue(
                    session,
                    kind="reclassify",
                    user_id=user_id,
                    scope_key=f"{user_id}:reclassify:all",
                    params={"reason": "type_mapping_changed"},
                )
            except ApiError as exc:
                # 入队失败不回滚导入：订正可由用户手动从批次页发起。
                log.warning("import.reclassify_enqueue_skipped", code=exc.code.value)

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
                "quarantined": finished.quarantined_count,
                "discarded": finished.discarded_count,
                "filled": fill_count,
                "covered": covered,
                "mapping_source": finished.mapping_source,
            },
        )
        await record(
            session,
            action="import.commit",
            actor_id=user_id,
            target_type="import_batch",
            target_id=finished.id,
            meta={
                "mode": mode,
                "inserted": inserted,
                "skipped": skipped,
                "filled": fill_count,
                "discarded": finished.discarded_count,
                "templates_written": templates_written,
                "types_written": types_written,
            },
        )

        result = _committed_result(finished, reused=False, filled_count=fill_count, covered=covered)
        await queue.finish(session, job_id, status=JobStatus.SUCCEEDED, result=result)
        await savepoint.commit()
        return result
    except BaseException as exc:
        if session.in_nested_transaction():
            await savepoint.rollback()
        return await _fail_job(queue, session, job_id, exc)


def _committed_result(
    batch: Any, *, reused: bool, filled_count: int = 0, covered: bool = False
) -> dict[str, Any]:
    return {
        "batch_id": batch.id,
        "status": batch.status,
        "inserted": batch.inserted_count,
        "skipped": batch.skipped_count,
        "quarantined": batch.quarantined_count,
        "discarded": batch.discarded_count,
        "filled_count": filled_count,
        "covered": covered,
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
    revisions_removed = await trade_repo.soft_delete_revisions_for_batch(
        user_id, batch_id, at=clock.now()
    )
    batch = await batch_repo.revoke(user_id, batch_id, reason=reason, at=clock.now())

    # 类型记忆：只删本批次新增/修改的（复用的旧类型保留，保护其他批次）。
    types_deleted = await ImportBillTypeRepository(session).delete_by_last_write(user_id, batch_id)
    # 字段模板：origin 批次撤销删除整条模板；增量批次只把 last_batch_id 回退到最近有效批次。
    mapping_deleted = False
    templates = ImportTemplateRepository(session)
    if batch.signature:
        template = await templates.by_signature(user_id, batch.signature)
        if template is not None:
            if template.origin_batch_id == batch.id:
                mapping_deleted = await templates.delete(user_id, template.id)
            elif template.last_batch_id == batch.id:
                latest = await batch_repo.latest_active_by_signature(
                    user_id, batch.signature, exclude_batch_id=batch.id
                )
                template.last_batch_id = (
                    latest.id if latest is not None else template.origin_batch_id
                )
                await session.flush()
    if mapping_deleted or types_deleted:
        batch.assumptions = {**batch.assumptions, "mapping_revoked": True}

    await publish(
        session,
        event_type=EVENT_REVOKED,
        aggregate="import_batch",
        aggregate_id=batch.id,
        user_id=user_id,
        payload={
            "batch_id": batch.id,
            "removed_trades": removed,
            "revisions_removed": revisions_removed,
            "reason": reason,
            "mapping_deleted": mapping_deleted,
            "types_deleted": types_deleted,
        },
    )
    await record(
        session,
        action="import.revoke",
        actor_id=actor_id or user_id,
        target_type="import_batch",
        target_id=batch.id,
        meta={
            "removed_trades": removed,
            "revisions_removed": revisions_removed,
            "reason": reason,
        },
        request_id=request_id,
        source="api",
    )
    await record(
        session,
        action="import.mapping_revoked",
        actor_id=actor_id or user_id,
        target_type="import_batch",
        target_id=batch.id,
        meta={
            "signature": batch.signature,
            "mapping_deleted": mapping_deleted,
            "types_deleted": types_deleted,
        },
        request_id=request_id,
        source="api",
    )
    return {
        "batch_id": batch.id,
        "status": batch.status,
        "revoked": True,
        "removed_trades": removed,
        "revisions_removed": revisions_removed,
        "revoked_at": batch.revoked_at,
        "mapping_revoked": {
            "mapping_deleted": mapping_deleted,
            "types_deleted": types_deleted,
        },
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
        await queue.progress_committed(user_id, job_id, percent=20, stage="writing", message=None)
        outcome = await revoke_batch_now(
            session, user_id=user_id, batch_id=batch_id, clock=clock, reason=reason
        )
        result = {
            "batch_id": outcome["batch_id"],
            "revoked": True,
            "removed_trades": outcome["removed_trades"],
            "mapping_revoked": outcome["mapping_revoked"],
        }
        await queue.finish(session, job_id, status=JobStatus.SUCCEEDED, result=result)
        await savepoint.commit()
        return result
    except BaseException as exc:
        if session.in_nested_transaction():
            await savepoint.rollback()
        return await _fail_job(queue, session, job_id, exc)


__all__ = ["commit_batch", "revoke_batch", "revoke_batch_now"]
