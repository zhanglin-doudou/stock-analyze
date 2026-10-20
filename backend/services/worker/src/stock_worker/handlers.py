"""任务处理器注册表（Architecture §5.2：处理器只做编排，业务在 ledger/review）。

签名统一为 `(queue, session, job, clock) -> dict | None`；
失败由 runner 依据抛出的 ApiError/异常决定重试或终态。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession
from stock_contracts.clock import Clock
from stock_platform.jobs import JobQueue

JobPayload = dict[str, Any]
JobHandler = Callable[[JobQueue, AsyncSession, JobPayload, Clock], Awaitable[dict[str, Any] | None]]


async def _import_commit(
    queue: JobQueue, session: AsyncSession, job: JobPayload, clock: Clock
) -> dict[str, Any] | None:
    from stock_ledger.application.imports import commit_batch

    return await commit_batch(queue, session, job, clock)


async def _import_revoke(
    queue: JobQueue, session: AsyncSession, job: JobPayload, clock: Clock
) -> dict[str, Any] | None:
    from stock_ledger.application.imports import revoke_batch

    return await revoke_batch(queue, session, job, clock)


async def _reclassify(
    queue: JobQueue, session: AsyncSession, job: JobPayload, clock: Clock
) -> dict[str, Any] | None:
    from stock_ledger.application.classify import reclassify_user

    return await reclassify_user(queue, session, job, clock)


HANDLERS: dict[str, JobHandler] = {
    "import_commit": _import_commit,
    "import_revoke": _import_revoke,
    "reclassify": _reclassify,
}
