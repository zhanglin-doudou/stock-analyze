"""任务处理器注册表（Architecture §5.2：处理器只做编排，业务在 ledger/review）。

签名统一为 `(queue, session, job, clock, market_data) -> dict | None`；
失败由 runner 依据抛出的 ApiError/异常决定重试或终态。

`market_data` 是 worker 懒装配的行情端口（Period2-16 D11）：只有需要行情估值的任务
（当前是 `build_assets`）才真正使用它；其余 handler 保留参数以统一签名。

集成约定（Period2-02）：导入提交 / 分类复算成功后自动入队 `rebuild_rounds`
（`scope_key=f"{user_id}:full"`），让回合表跟上明细变化。入队失败（配额/单飞冲突）
只记日志、绝不改判已成功的主任务——回合会在下一次 rebuild 时追平，宁可延迟不可回滚。
撤销（API-015）是同步路径，其自动入队由 API 层负责，不在本文件重复。
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession
from stock_contracts.clock import Clock
from stock_contracts.errors import ApiError
from stock_platform.jobs import JobQueue
from stock_platform.obs import get_logger
from stock_platform.ports import MarketDataSource

JobPayload = dict[str, Any]
JobHandler = Callable[
    [JobQueue, AsyncSession, JobPayload, Clock, MarketDataSource],
    Awaitable[dict[str, Any] | None],
]

log = get_logger("stock_worker.handlers")


async def _enqueue_rebuild(
    queue: JobQueue, session: AsyncSession, job: JobPayload, *, reason: str
) -> None:
    """主任务成功后投递全量回合重建；失败只记日志（不回滚已成功的主任务）。"""
    user_id = uuid.UUID(str(job["user_id"]))
    try:
        await queue.enqueue(
            session,
            kind="rebuild_rounds",
            user_id=user_id,
            scope_key=f"{user_id}:full",
            params={"scope": "full", "reason": reason},
        )
    except ApiError as exc:
        # 配额超限 / 已有同 scope 的异参任务在跑：由那次任务或下一次 rebuild 追平。
        log.warning(
            "worker.rebuild_enqueue_skipped",
            user_id=str(user_id),
            reason=reason,
            code=exc.code.value,
        )


async def _import_commit(
    queue: JobQueue,
    session: AsyncSession,
    job: JobPayload,
    clock: Clock,
    _market_data: MarketDataSource,
) -> dict[str, Any] | None:
    from stock_ledger.application.imports import commit_batch

    result = await commit_batch(queue, session, job, clock)
    await _enqueue_rebuild(queue, session, job, reason="import_commit")
    return result


async def _import_revoke(
    queue: JobQueue,
    session: AsyncSession,
    job: JobPayload,
    clock: Clock,
    _market_data: MarketDataSource,
) -> dict[str, Any] | None:
    from stock_ledger.application.imports import revoke_batch

    result = await revoke_batch(queue, session, job, clock)
    await _enqueue_rebuild(queue, session, job, reason="import_revoke")
    return result


async def _reclassify(
    queue: JobQueue,
    session: AsyncSession,
    job: JobPayload,
    clock: Clock,
    _market_data: MarketDataSource,
) -> dict[str, Any] | None:
    from stock_ledger.application.classify import reclassify_user

    result = await reclassify_user(queue, session, job, clock)
    await _enqueue_rebuild(queue, session, job, reason="reclassify")
    return result


async def _rebuild_rounds(
    queue: JobQueue,
    session: AsyncSession,
    job: JobPayload,
    clock: Clock,
    _market_data: MarketDataSource,
) -> dict[str, Any] | None:
    from stock_review.application.rebuild import rebuild_rounds_job

    return await rebuild_rounds_job(queue, session, job, clock)


async def _build_assets(
    queue: JobQueue,
    session: AsyncSession,
    job: JobPayload,
    clock: Clock,
    market_data: MarketDataSource,
) -> dict[str, Any] | None:
    """Period2-16（API-082）：构建/增量更新每日资产快照（行情估值需要行情端口）。"""
    from stock_review.application.assets import build_assets_job

    return await build_assets_job(queue, session, job, clock, market_data)


async def _rebuild_period(
    queue: JobQueue,
    session: AsyncSession,
    job: JobPayload,
    clock: Clock,
    market_data: MarketDataSource,
) -> dict[str, Any] | None:
    """API-083：按月一键编排 rounds → assets → nav → metrics。"""
    from stock_review.application.rebuild_period import rebuild_period_job

    return await rebuild_period_job(queue, session, job, clock, market_data)


HANDLERS: dict[str, JobHandler] = {
    "import_commit": _import_commit,
    "import_revoke": _import_revoke,
    "reclassify": _reclassify,
    "rebuild_rounds": _rebuild_rounds,
    "build_assets": _build_assets,
    "rebuild_period": _rebuild_period,
}
