"""WorkerRunner 异常收口 / 心跳续租单测（G2）。

覆盖检查单 G2 的第 1、5 条：
  * 执行期间独立事务心跳续租 → 长 handler 不被 reaper 回收、attempt 不涨；
  * handler 抛未知异常 → 终态 failed + INTERNAL_ERROR，消费循环继续；
  * handler 抛 ApiError → fail_or_retry 回 pending（带业务错误码）。
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest_asyncio
from sqlalchemy import select
from stock_contracts.clock import Clock, FrozenClock, SystemClock
from stock_contracts.enums import JobStatus
from stock_contracts.errors import ApiError, ErrorCode
from stock_platform.config import Settings
from stock_platform.db import Database
from stock_platform.jobs import JobQueue
from stock_platform.models import Job, JobEvent
from stock_worker.handlers import HANDLERS
from stock_worker.runner import WorkerRunner

JobPayload = dict[str, Any]


@pytest_asyncio.fixture
async def runner(settings: Settings) -> AsyncIterator[WorkerRunner]:
    worker = WorkerRunner(settings, concurrency=1)
    try:
        yield worker
    finally:
        await worker.db.dispose()


async def _enqueue(
    runner: WorkerRunner,
    user_id: uuid.UUID,
    *,
    kind: str,
    max_attempts: int = 3,
) -> str:
    async with runner.db.session(begin=True) as session:
        job_id, _ = await runner.queue.enqueue(
            session,
            kind=kind,
            user_id=user_id,
            scope_key=f"{user_id}:{uuid.uuid4().hex[:8]}",
            params={},
            max_attempts=max_attempts,
        )
    return job_id


async def _load(db: Database, queue: JobQueue, job_id: str) -> Job:
    async with db.session(begin=True) as session:
        job = await queue.get(session, job_id)
    assert job is not None
    return job


async def _event_types(db: Database, job_id: str) -> list[str]:
    async with db.session(begin=True) as session:
        rows = await session.scalars(
            select(JobEvent).where(JobEvent.job_id == job_id).order_by(JobEvent.seq)
        )
        return [row.type for row in rows]


# ── 心跳：长 handler 不被回收 ───────────────────────────────


async def test_heartbeat_keeps_long_handler_alive(
    runner: WorkerRunner,
    db: Database,
    user_id: uuid.UUID,
    monkeypatch: Any,
) -> None:
    # 把租约压到 0.5s、心跳 0.1s：不续租的话 0.7s 处的 reaper 必然回收。
    monkeypatch.setattr("stock_platform.jobs.LEASE_SECONDS", 0.5)
    monkeypatch.setattr("stock_platform.jobs.HEARTBEAT_SECONDS", 0.1)
    job_id = await _enqueue(runner, user_id, kind="slow_test")

    async def slow_handler(
        queue: JobQueue, session: Any, job: JobPayload, clock: Clock
    ) -> dict[str, Any]:
        await asyncio.sleep(1.0)
        await queue.finish(session, job["id"], status=JobStatus.SUCCEEDED, result={"ok": True})
        return {"ok": True}

    monkeypatch.setitem(HANDLERS, "slow_test", slow_handler)
    claimed = await runner._claim_one()
    assert claimed is not None and claimed["id"] == job_id

    real_queue = JobQueue(db, SystemClock())  # reaper 侧用真实时钟
    task = asyncio.create_task(runner._execute(claimed))
    try:
        await asyncio.sleep(0.7)
        async with db.session(begin=True) as session:
            assert await real_queue.reap_expired_leases(session) == 0
        async with db.session(begin=True) as session:
            current = await real_queue.get(session, job_id)
            assert current is not None
            assert current.status == JobStatus.RUNNING.value
            assert current.attempt == 1  # 未被回收重投
    finally:
        await task

    job = await _load(db, real_queue, job_id)
    assert job.status == JobStatus.SUCCEEDED.value
    assert job.finished_at is not None


# ── 异常收口：不让消费循环退出 ─────────────────────────────


async def test_unknown_exception_marks_failed_and_loop_continues(
    runner: WorkerRunner,
    db: Database,
    user_id: uuid.UUID,
    monkeypatch: Any,
) -> None:
    boom_id = await _enqueue(runner, user_id, kind="boom")
    ok_id = await _enqueue(runner, user_id, kind="ok")

    async def boom_handler(
        queue: JobQueue, session: Any, job: JobPayload, clock: Clock
    ) -> dict[str, Any]:
        raise RuntimeError("handler 内部炸了")

    async def ok_handler(
        queue: JobQueue, session: Any, job: JobPayload, clock: Clock
    ) -> dict[str, Any]:
        await queue.finish(session, job["id"], status=JobStatus.SUCCEEDED, result={"ok": True})
        return {"ok": True}

    monkeypatch.setitem(HANDLERS, "boom", boom_handler)
    monkeypatch.setitem(HANDLERS, "ok", ok_handler)

    claimed = [await runner._claim_one(), await runner._claim_one()]
    assert all(job is not None for job in claimed)
    pending: list[JobPayload] = [job for job in claimed if job is not None]

    async def fake_claim() -> JobPayload | None:
        if pending:
            return pending.pop(0)
        runner._stopping = True
        return None

    monkeypatch.setattr(runner, "_claim_one", fake_claim)
    await runner._loop()  # 不能抛：坏任务之后仍要消费下一个

    boom_job = await _load(db, runner.queue, boom_id)
    assert boom_job.status == JobStatus.FAILED.value
    assert boom_job.error_code == ErrorCode.INTERNAL_ERROR.value
    assert boom_job.finished_at is not None

    ok_job = await _load(db, runner.queue, ok_id)
    assert ok_job.status == JobStatus.SUCCEEDED.value


async def test_api_error_retries_via_fail_or_retry(
    runner: WorkerRunner,
    db: Database,
    user_id: uuid.UUID,
    monkeypatch: Any,
) -> None:
    job_id = await _enqueue(runner, user_id, kind="bad_api", max_attempts=3)

    async def bad_api_handler(
        queue: JobQueue, session: Any, job: JobPayload, clock: Clock
    ) -> dict[str, Any]:
        raise ApiError(ErrorCode.VALIDATION_ERROR, detail="参数不合法")

    monkeypatch.setitem(HANDLERS, "bad_api", bad_api_handler)
    claimed = await runner._claim_one()
    assert claimed is not None
    await runner._execute(claimed)  # 不抛异常

    job = await _load(db, runner.queue, job_id)
    assert job.status == JobStatus.PENDING.value
    assert job.error_code == ErrorCode.VALIDATION_ERROR.value
    assert job.lease_until is None
    assert "retrying" in await _event_types(db, job_id)


async def test_handler_crashing_after_finish_does_not_duplicate_finish(
    runner: WorkerRunner,
    db: Database,
    user_id: uuid.UUID,
    monkeypatch: Any,
) -> None:
    """handler 已收口但事务随异常回滚时，外层兜底只记录一次 failed（终态一致）。"""
    job_id = await _enqueue(runner, user_id, kind="boom_after_finish")

    async def boom_after_finish(
        queue: JobQueue, session: Any, job: JobPayload, clock: Clock
    ) -> dict[str, Any]:
        await queue.finish(session, job["id"], status=JobStatus.SUCCEEDED, result={"ok": True})
        raise RuntimeError("收口后炸")

    monkeypatch.setitem(HANDLERS, "boom_after_finish", boom_after_finish)
    claimed = await runner._claim_one()
    assert claimed is not None
    await runner._execute(claimed)

    job = await _load(db, runner.queue, job_id)
    # handler 事务已回滚，收口必须由外层补一次；不能停在 running。
    assert job.status == JobStatus.FAILED.value
    assert job.error_code == ErrorCode.INTERNAL_ERROR.value
    terminal_events = [
        event for event in await _event_types(db, job_id) if event in ("failed", "succeeded")
    ]
    assert terminal_events == ["failed"]


async def test_frozen_clock_type_is_clock() -> None:
    """防止 conftest 时钟夹具与端口脱节（Clock Protocol 需 runtime_checkable）。"""
    assert isinstance(FrozenClock("2026-06-30T09:00:00+00:00"), Clock)
