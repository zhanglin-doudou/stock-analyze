"""一键构建编排（API-083）：rounds → assets → nav → metrics。

- period token 支持 `YYYY-MM`（月）/ `YYYY`（年）/ `YYYY-MM-DD..YYYY-MM-DD`（自定义区间）；
- 单 job 单事务（runner `tenant_tx`）：任一阶段失败整体回滚，重跑即阶段级恢复
  （rounds 确定性 ID 删后重插、assets 指纹/水位线增量、nav UPSERT、metrics UPSERT）；
- 阶段间检查取消标记（API-064 检查点语义），progress 单调不减；
- metrics 阶段消费同一事务内的 asset_daily/nav —— 与 /nav 同一资产口径；
- 物化含 blocked/stale 状态行（`persist_outcome(force=True)`），非 ok 不硬算数值；
  自定义区间不落 `metric_result`（无界缓存）。
"""

from __future__ import annotations

import logging
import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession
from stock_contracts.clock import Clock
from stock_contracts.enums import JobStatus
from stock_contracts.errors import ApiError, ErrorCode
from stock_platform.jobs import JobQueue
from stock_platform.ports import MarketDataSource

from stock_review.application.assets import build_assets
from stock_review.application.metrics import batch_metrics
from stock_review.application.nav import compute_and_store_nav
from stock_review.application.rebuild import parse_period, rebuild_rounds_for_user
from stock_review.metrics.context import period_is_cacheable
from stock_review.metrics.persist import persist_outcome
from stock_review.repositories.metrics import MetricResultRepository

logger = logging.getLogger(__name__)

__all__ = ["rebuild_period_job"]


def _jsonable(value: Any) -> str | None:
    return None if value is None else value.isoformat()


async def rebuild_period_job(
    queue: JobQueue,
    session: AsyncSession,
    job: dict[str, Any],
    clock: Clock,
    market_data: MarketDataSource,
) -> dict[str, Any]:
    """worker handler：rounds(10) → assets(40) → nav(60) → metrics(85) → finish(100)。"""
    user_id = uuid.UUID(str(job["user_id"]))
    job_id = str(job["id"])
    params: dict[str, Any] = dict(job.get("params") or {})
    period = str(params.get("period") or "")
    include_exploratory = bool(params.get("include_exploratory", False))
    rf_annual = Decimal(str(params.get("rf_annual") or "0.015"))
    if not period:
        raise ApiError(
            ErrorCode.INVALID_PERIOD,
            detail="rebuild_period 需要 period(YYYY-MM / YYYY / YYYY-MM-DD..YYYY-MM-DD)",
        )
    period_start, period_end = parse_period(period, today=clock.today())
    cacheable = period_is_cacheable(period)

    async def cancel_checkpoint() -> bool:
        if await queue.is_cancel_requested(session, job_id):
            await queue.finish(
                session, job_id, status=JobStatus.CANCELLED, result={"cancelled": True}
            )
            return True
        return False

    # 阶段①：rounds（period = 脏窗口；受影响证券全历史重建）
    await queue.progress(session, job_id, percent=10, stage="rounds", message=None)
    if await cancel_checkpoint():
        return {"job_id": job_id, "cancelled": True}
    rounds = await rebuild_rounds_for_user(
        session,
        user_id=user_id,
        clock=clock,
        scope="period",
        period_start=period_start,
        period_end=period_end,
    )

    # 阶段②：assets（auto 增量；指纹/水位线无变化 → no-op）
    await queue.progress(session, job_id, percent=40, stage="assets", message=None)
    if await cancel_checkpoint():
        return {"job_id": job_id, "cancelled": True}
    assets = await build_assets(
        session, user_id=user_id, clock=clock, market_data=market_data, mode="auto"
    )

    # 阶段③：nav（消费同事务 asset_daily）
    await queue.progress(session, job_id, percent=60, stage="nav", message=None)
    if await cancel_checkpoint():
        return {"job_id": job_id, "cancelled": True}
    nav = await compute_and_store_nav(
        session,
        user_id=user_id,
        period_start=period_start,
        period_end=period_end,
        market_data=market_data,
        clock=clock,
    )

    # 阶段④：metrics（默认核心 10；状态矩阵由引擎评估，blocked/stale 不硬算）
    await queue.progress(session, job_id, percent=85, stage="metrics", message=None)
    if await cancel_checkpoint():
        return {"job_id": job_id, "cancelled": True}
    batch = await batch_metrics(
        session,
        user_id=user_id,
        clock=clock,
        market_data=market_data,
        rf_annual=rf_annual,
        period=period,
        date_from=None,
        date_to=None,
        benchmark=None,
        ids=None,
        domain=None,
        include_exploratory=include_exploratory,
    )

    # 交叉断言：指标 data_as_of 不得晚于资产口径（同一事务内同一 asset_daily）。
    if assets.data_as_of is not None and batch.data_as_of > assets.data_as_of:
        raise ApiError(
            ErrorCode.INTERNAL_ERROR,
            detail=(
                f"口径分叉：metrics data_as_of {batch.data_as_of} 晚于 "
                f"assets data_as_of {assets.data_as_of}"
            ),
        )

    repository = MetricResultRepository(session)
    persisted = 0
    statuses: dict[str, int] = {}
    for outcome in batch.all_outcomes:
        statuses[outcome.status] = statuses.get(outcome.status, 0) + 1
        if not cacheable:
            continue
        try:
            async with session.begin_nested():
                if await persist_outcome(repository, user_id, outcome, force=True):
                    persisted += 1
        except Exception:
            logger.warning("rebuild_period persist failed: %s", outcome.metric_id, exc_info=True)

    logger.info(
        "rebuild_period metrics: job=%s period=%s computed=%d persisted=%d statuses=%s",
        job_id,
        period,
        len(batch.all_outcomes),
        persisted,
        statuses,
    )
    degraded_outcomes = [
        f"{outcome.metric_id}:{outcome.status}:{outcome.reason}"
        for outcome in batch.all_outcomes
        if outcome.status != "ok" or outcome.sample_status != "ok"
    ]
    if degraded_outcomes:
        logger.warning(
            "rebuild_period metrics non-ok: job=%s period=%s details=%s",
            job_id,
            period,
            degraded_outcomes,
        )

    result: dict[str, Any] = {
        "period": period,
        "include_exploratory": include_exploratory,
        "rounds": {
            "rounds_total": rounds.rounds_total,
            "rounds_closed": rounds.rounds_closed,
            "rounds_open": rounds.rounds_open,
        },
        "assets": {
            "mode": assets.mode,
            "rows_written": assets.rows_written,
            "status": assets.status,
            "data_as_of": _jsonable(assets.data_as_of),
        },
        "nav": {
            "status": "blocked" if nav.blocked_reasons else ("degraded" if nav.degraded else "ok"),
        },
        "metrics": {
            "computed": len(batch.all_outcomes),
            "persisted": persisted,
            "statuses": statuses,
            "data_as_of": _jsonable(batch.data_as_of),
        },
    }
    await queue.finish(session, job_id, status=JobStatus.SUCCEEDED, result=result)
    return {"job_id": job_id, **result}
